"""What the endpoints do with observations: post (FR-1..FR-3), react (FR-4), get (FR-5), and the
skills' `/match` and `/neighbors`. Reads come from the index; writes go through the store's queue
and are pushed before the reply.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fieldnotes_contracts import (
    POST_CANDIDATES,
    POST_EMOJI,
    MatchReply,
    MatchRequest,
    NeighborsReply,
    Observation,
    PostReply,
    PostRequest,
    ProblemType,
    Reaction,
    ReactReply,
    ReactRequest,
)

from .document import Document, DocumentError, new_document
from .errors import ProblemException, not_found
from .index import Index, embedded_text, observation_path
from .matching import Matcher, candidate, reaction_counts
from .store import Commit, Store
from .ulid import new_ulid

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def invalid_file(id_: str, exc: DocumentError) -> ProblemException:
    return ProblemException(
        409,
        ProblemType.conflict,
        f"the file of observation {id_} is not valid",
        detail=f"{exc}; the reconciler or the operator has to repair it in the store",
    )


class Observations:
    def __init__(self, store: Store, index: Index, matcher: Matcher, clock: Clock) -> None:
        self.store = store
        self.index = index
        self.matcher = matcher
        self.clock = clock

    def get(self, id_: str) -> Observation:
        entry = self.index.get(id_)
        if entry is None:
            raise not_found(id_)
        return entry.observation

    async def post(self, request: PostRequest, client: str) -> PostReply:
        if not request.force:
            matches = await self.matcher.match(
                embedded_text(request.area, request.text), POST_CANDIDATES
            )
            if matches:
                return PostReply(candidates=[candidate(match) for match in matches])

        id_ = new_ulid(self.clock())

        def create(root: Path) -> tuple[str, Commit]:
            reaction = Reaction(
                at=self.clock(),
                emoji=POST_EMOJI,
                repo=request.repo,
                session=request.session,
                client=client,
                text=request.text,
            )
            document = new_document(
                id_=id_,
                area=request.area,
                category=request.category,
                text=request.text,
                reaction=reaction,
            )
            path = observation_path(id_)
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(document.text)
            return id_, Commit((path,), f"post {id_} ({client}): {request.area}")

        await self.store.write(create)
        logger.info("post %s by %s from %s", id_, client, request.repo)
        return PostReply(id=id_)

    async def react(self, id_: str, request: ReactRequest, client: str) -> ReactReply:
        self.get(id_)

        def append(root: Path) -> tuple[list[str], Commit]:
            file = root / observation_path(id_)
            if not file.exists():
                raise not_found(id_)
            document = Document(file.read_text())
            try:
                repos = document.observation.repos
            except DocumentError as exc:
                raise invalid_file(id_, exc) from exc
            now = self.clock()
            reaction = Reaction(
                at=now,
                emoji=request.emoji,
                repo=request.repo,
                session=request.session,
                client=client,
                text=request.text,
            )
            fields: dict[str, object] = {"last_seen": now, "last_updated": now}
            if request.repo not in repos:
                fields["repos"] = [*repos, request.repo]
            document = document.with_reaction(reaction).with_fields(**fields)
            file.write_text(document.text)
            message = f"react {id_} {request.emoji} ({client})"
            return reaction_counts(document.observation), Commit((observation_path(id_),), message)

        reactions = await self.store.write(append)
        logger.info("react %s %s by %s from %s", id_, request.emoji, client, request.repo)
        return ReactReply(id=id_, reactions=reactions)

    async def match(self, request: MatchRequest) -> MatchReply:
        text = embedded_text(request.area, request.text) if request.area else request.text
        matches = await self.matcher.match(text, request.k, rerank=request.rerank)
        return MatchReply(candidates=[candidate(match) for match in matches])

    async def neighbors(self, id_: str, k: int) -> NeighborsReply:
        entry = self.index.get(id_)
        if entry is None:
            raise not_found(id_)
        matches = await self.matcher.match(entry.text, k, thresholds=False, exclude=id_)
        return NeighborsReply(id=id_, neighbors=[candidate(match) for match in matches])
