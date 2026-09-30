"""The operator's writes to the triage items (FR-24 to FR-26): a ruling, its take-back and a
submit, each one commit through the store's write queue, pushed before the caller hears back.

Every refusal is decided on the item as the store holds it at the tip the write lands on, inside
the write's edit: the triage index can trail a skill's push. An item is written back in the
spelling it was read in, `ruling` alone changed, so a commit's diff is the ruling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.fieldnotes.errors import ProblemException
from app.fieldnotes.metrics import Metrics
from app.fieldnotes.observations import Clock
from app.fieldnotes.store import Commit, Store
from app.fieldnotes.triage import (
    TRIAGE,
    ItemFault,
    RulingRequest,
    TriageItem,
    item_path,
    parse_item,
    spelled,
    stamp,
)
from fieldnotes_contracts import ProblemType

logger = logging.getLogger(__name__)

Edited = tuple[TriageItem, Commit | None]


def _no_item(id_: str) -> ProblemException:
    return ProblemException(
        404,
        ProblemType.not_found,
        f"no triage item for observation {id_}",
        detail="the actioner stamped it or the reconciler withdrew it; fetch the queue again",
    )


def _conflict(id_: str, title: str, detail: str) -> ProblemException:
    return ProblemException(
        409,
        ProblemType.conflict,
        f"the triage item for observation {id_} {title}",
        detail=detail,
    )


def _open(root: Path, id_: str, written: str) -> dict[str, Any]:
    """The item's JSON at the tip, if the operator may still change its ruling."""
    file = root / item_path(id_)
    if not file.exists():
        raise _no_item(id_)
    try:
        data, item = parse_item(file.name, file.read_text())
    except ItemFault as exc:
        raise _conflict(
            id_, "is not valid", f"{exc}; the reconciler has to repair it in the store"
        ) from exc
    if item.submitted:
        raise _conflict(
            id_,
            "is submitted",
            "it is the actioner's until the actioner stamps it or returns it with a question",
        )
    if item.written != written:
        raise _conflict(
            id_,
            "was rewritten after the page loaded",
            "nothing was written; fetch the queue again for the item as it stands",
        )
    return data


def _set_ruling(
    root: Path,
    id_: str,
    data: dict[str, Any],
    ruling: dict[str, Any] | None,
    message: str,
) -> Edited:
    unchanged = data["ruling"] == ruling
    data["ruling"] = ruling
    item = TriageItem.model_validate(data)
    if unchanged:
        return item, None
    (root / item_path(id_)).write_text(spelled(data))
    return item, Commit((item_path(id_),), message)


class Rulings:
    def __init__(self, store: Store, clock: Clock, metrics: Metrics) -> None:
        self.store = store
        self.clock = clock
        self.metrics = metrics

    def rule(self, id_: str, request: RulingRequest) -> TriageItem:
        """FR-25: the ruling over any draft; the item as it now stands."""
        verb = request.verb.value

        def edit(root: Path) -> Edited:
            data = _open(root, id_, request.written)
            at = stamp(self.clock())
            ruling = {"verb": verb, "note": request.note, "at": at, "submitted": None}
            return _set_ruling(root, id_, data, ruling, f"rule {id_} {verb} (operator)")

        item: TriageItem = self.store.write(edit)
        self.metrics.rulings.labels(verb).inc()
        logger.info("rule %s %s (operator)", id_, verb)
        return item

    def take_back(self, id_: str, written: str) -> TriageItem:
        """FR-25: the draft cleared; on an item without a ruling, nothing written."""

        def edit(root: Path) -> Edited:
            data = _open(root, id_, written)
            return _set_ruling(root, id_, data, None, f"unrule {id_} (operator)")

        item: TriageItem = self.store.write(edit)
        logger.info("unrule %s (operator)", id_)
        return item

    def submit(self) -> list[str]:
        """FR-26: every ruled, unsubmitted item submitted in one commit; the ids submitted."""

        def edit(root: Path) -> tuple[list[str], Commit | None]:
            now = stamp(self.clock())
            ids: list[str] = []
            for file in sorted((root / TRIAGE).glob("*.json")):
                try:
                    data, item = parse_item(file.name, file.read_text())
                except ItemFault:
                    continue  # not in the queue, and logged there
                if item.submitted or not item.ruled:
                    continue
                data["ruling"]["submitted"] = now
                file.write_text(spelled(data))
                ids.append(item.observation)
            if not ids:
                return ids, None
            paths = tuple(item_path(id_) for id_ in ids)
            return ids, Commit(paths, f"submit {len(ids)} (operator)")

        ids: list[str] = self.store.write(edit)
        logger.info(
            "submit %d (operator): %s", len(ids), ", ".join(ids) or "nothing ruled"
        )
        return ids
