"""What the endpoints do with observations: post (FR-1..FR-3), react (FR-4), get (FR-5), the
skills' `/match` and `/neighbors`, and the board sync (FR-21, FR-22). Reads come from the index;
writes go through the store's queue and are pushed before the reply.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from fieldnotes_contracts import (
    POST_CANDIDATES,
    POST_EMOJI,
    BoardSyncReply,
    MatchReply,
    MatchRequest,
    NeighborsReply,
    Observation,
    Outcome,
    PostReply,
    PostRequest,
    ProblemType,
    Reaction,
    ReactReply,
    ReactRequest,
    Status,
)

from .board import Board, Card, CardMissing
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


def no_board() -> ProblemException:
    return ProblemException(
        503,
        ProblemType.board_unreachable,
        "no board is configured",
        detail="the API reads cards from YouTrack once FIELDNOTES_YOUTRACK_URL and "
        "FIELDNOTES_YOUTRACK_TOKEN are set",
    )


# The statuses a card's resolution closes (design, "Observation lifecycle": raised -> closed, and
# a closed one's outcome follows a later change). An open or proposed observation that still has a
# card was reopened; its old card's resolution is history.
_CLOSABLE = frozenset({Status.raised, Status.closed})


class Observations:
    def __init__(
        self,
        store: Store,
        index: Index,
        matcher: Matcher,
        clock: Clock,
        board: Board | None,
        outcomes: Mapping[str, Outcome],
    ) -> None:
        self.store = store
        self.index = index
        self.matcher = matcher
        self.clock = clock
        self.board = board
        self.outcomes = outcomes
        self._syncs: set[asyncio.Task[BoardSyncReply]] = set()
        self._settling: dict[str, asyncio.Task[BoardSyncReply]] = {}

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
        matches = await self.matcher.match(text, request.k)
        return MatchReply(candidates=[candidate(match) for match in matches])

    async def neighbors(self, id_: str, k: int) -> NeighborsReply:
        entry = self.index.get(id_)
        if entry is None:
            raise not_found(id_)
        matches = await self.matcher.match(entry.text, k, thresholds=False, exclude=id_)
        return NeighborsReply(id=id_, neighbors=[candidate(match) for match in matches])

    async def board_sync(self, id_: str) -> BoardSyncReply:
        """FR-21, FR-22: read the observation's card from the board and apply it. Writes only when
        the card changed since the last sync: then `card_updated` and `last_updated` move, the
        outcome follows the resolution field, and a `Resolved:` comment supplies the pointer."""
        observation = self.get(id_)
        if observation.card is None:
            raise ProblemException(
                409,
                ProblemType.conflict,
                f"observation {id_} has no card",
                detail="a board sync reads the issue named in the observation's card field",
            )
        if self.board is None:
            raise no_board()
        try:
            card = await self.board.card(observation.card)
        except CardMissing as exc:
            raise ProblemException(
                409,
                ProblemType.conflict,
                f"the card {observation.card} is not on the board",
                detail=f"observation {id_} names an issue YouTrack does not have; correct its card",
            ) from exc
        return await self.store.write(lambda root: self._apply(root, id_, card))

    def _apply(self, root: Path, id_: str, card: Card) -> tuple[BoardSyncReply, Commit | None]:
        file = root / observation_path(id_)
        if not file.exists():
            raise not_found(id_)
        document = Document(file.read_text())
        try:
            observation = document.observation
        except DocumentError as exc:
            raise invalid_file(id_, exc) from exc
        unchanged = (
            observation.card is None
            or observation.card.upper() != card.id.upper()
            or (observation.card_updated is not None and card.updated <= observation.card_updated)
        )
        if unchanged:
            return _synced(observation, changed=False), None

        fields: dict[str, object] = {"card_updated": card.updated, "last_updated": self.clock()}
        outcome = self.outcomes.get(card.resolution) if card.resolution else None
        if outcome is not None and observation.status in _CLOSABLE:
            fields |= {"status": Status.closed, "outcome": outcome}
        if card.pointer is not None and card.pointer != observation.pointer:
            fields["pointer"] = card.pointer
        document = document.with_fields(**fields)
        file.write_text(document.text)
        message = f"board-sync {id_} {card.id}"
        return _synced(document.observation, changed=True), Commit(
            (observation_path(id_),), message
        )

    def sync_later(self, id_: str, settle: float = 0.0) -> None:
        """Queue a board sync and return at once: the YouTrack webhook's answer cannot wait for
        a read from YouTrack and a push. A failure is logged.

        The card is read `settle` seconds from now, because YouTrack sends a delivery before it
        commits the change the delivery is about. A delivery that arrives while an earlier one for
        the same observation is still waiting restarts the wait: one read then covers both. The
        wait is spent here, not in the store's write queue."""
        waiting = self._settling.pop(id_, None)
        if waiting is not None:
            waiting.cancel()

        async def settled() -> BoardSyncReply:
            await asyncio.sleep(settle)
            if self._settling.get(id_) is task:
                del self._settling[id_]
            return await self.board_sync(id_)

        task = asyncio.create_task(settled(), name=f"board-sync {id_}")
        self._settling[id_] = task
        self._syncs.add(task)
        task.add_done_callback(self._synced)

    def _synced(self, task: asyncio.Task[BoardSyncReply]) -> None:
        self._syncs.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("%s failed", task.get_name(), exc_info=exc)
            return
        reply = task.result()
        if reply.changed:
            logger.info("board-sync %s: %s is %s", reply.id, reply.card, reply.status)


def _synced(observation: Observation, *, changed: bool) -> BoardSyncReply:
    return BoardSyncReply(
        id=observation.id,
        card=observation.card or "",
        changed=changed,
        status=observation.status,
        outcome=observation.outcome,
        pointer=observation.pointer,
        card_updated=observation.card_updated,
    )
