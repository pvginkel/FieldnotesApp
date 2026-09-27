"""The triage index and the operator's queue (FR-23; design, "Triage").

A triage item is one JSON file per observation the reconciler lists: `triage/<id>.json` while it is
open, `triage/done/…` once the actioner has stamped it, `triage/archive/…` for what came before the
items. The index holds every open item, submitted ones included; `done/` and `archive/` are not
read. The store's listener keeps it current as it does the observation index: every path at start,
then the paths each pull or write changed.

The item rules are the app's own copy of the store's (`skills/reconciler/reconcile.py` in the store
repo: `ITEM_KEYS`, `SNAPSHOT_KEYS`, `item_faults`), for an open item. An item file that is not JSON,
breaks a rule, or does not parse as an item is logged and left out until an edit fixes it; it never
fails the queue.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    ValidationError,
    model_validator,
)

from app.fieldnotes.index import Index
from fieldnotes_contracts import ID_PATTERN, TEXT_MAX, Observation

logger = logging.getLogger(__name__)

TRIAGE = "triage"
_PATH = re.compile(rf"^{TRIAGE}/([^/]+)\.json$")
_ID = re.compile(ID_PATTERN)

ITEM_KEYS = (
    "observation",
    "written",
    "headline",
    "ask",
    "evidence",
    "recommendation",
    "impact",
    "snapshot",
    "reports",
    "ruling",
    "question",
    "actioned",
)
ITEM_TEXTS = ("headline", "ask", "evidence", "recommendation", "impact")
SNAPSHOT_KEYS = (
    "status",
    "category",
    "area",
    "canonical",
    "repos",
    "card",
    "outcome",
    "reason",
    "created",
    "last_seen",
)
REPORT_KEYS = ("at", "emoji", "repo", "session", "client", "text")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def item_path(id_: str) -> str:
    return f"{TRIAGE}/{id_}.json"


def stamp(at: datetime) -> str:
    """A time in the store's one spelling."""
    return at.astimezone(UTC).strftime(TIME_FORMAT)


def spelled(item: dict[str, Any]) -> str:
    """An item as the store's writers write it: two spaces, UTF-8 as is, a trailing newline. The
    keys keep the order they were read in, which a store writer made `ITEM_KEYS`."""
    return json.dumps(item, indent=2, ensure_ascii=False) + "\n"


# -- the store's rules ---------------------------------------------------------------------------


class Verb(StrEnum):
    yes = "yes"
    no = "no"
    later = "later"


def is_time(value: str) -> bool:
    """The store's one spelling of a timestamp: UTC, whole seconds, `Z`."""
    try:
        datetime.strptime(value, TIME_FORMAT)
    except ValueError:
        return False
    return True


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def item_faults(name: str, item: dict[str, Any]) -> list[str]:
    """What the store's check finds wrong with the open item `triage/<name>`; none when it
    passes."""
    found = [f"{key} is missing" for key in ITEM_KEYS if key not in item]
    id_ = item.get("observation")
    if not isinstance(id_, str) or not _ID.match(id_):
        found.append(f"observation {id_!r} is not a ULID")
    elif name != f"{id_}.json":
        found.append(f"the file name is not {id_}.json")
    if "written" in item and not is_time(str(item["written"])):
        found.append(f"written {item['written']!r} is not a UTC timestamp")
    found += [f"{key} is not text" for key in ITEM_TEXTS if key in item and not _is_text(item[key])]
    if "snapshot" in item and not isinstance(item["snapshot"], dict):
        found.append("snapshot is not an object")
    reports = item.get("reports")
    if "reports" in item and not isinstance(reports, list):
        found.append("reports is not a list")
    for position, report in enumerate(reports if isinstance(reports, list) else [], 1):
        if not isinstance(report, dict) or any(report.get(k) is None for k in REPORT_KEYS[:3]):
            found.append(f"report {position} lacks at, emoji or repo")
    ruling = item.get("ruling")
    if ruling is not None:
        if not isinstance(ruling, dict) or ruling.get("verb") not in tuple(Verb):
            found.append(f"the ruling's verb is not one of {', '.join(Verb)}")
        elif not isinstance(ruling.get("note"), str):
            found.append("the ruling's note is not text")
        elif not is_time(str(ruling.get("at"))):
            found.append("the ruling's at is not a UTC timestamp")
        elif ruling.get("submitted") is not None and not is_time(str(ruling["submitted"])):
            found.append("the ruling's submitted is neither null nor a UTC timestamp")
    question = item.get("question")
    if question is not None and (
        not isinstance(question, dict)
        or not is_time(str(question.get("at")))
        or not _is_text(question.get("text"))
    ):
        found.append("the question is not an object with at and text")
    if item.get("actioned") is not None:
        found.append("a stamped item is still open: it belongs in done/")
    return found


# -- the item as the store holds it --------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Snapshot(_Model):
    """An observation's fields as an item's snapshot spells them; the queue's live observations
    are spelled the same, so an unchanged one compares equal as strings."""

    status: str
    category: str
    area: str
    canonical: str
    repos: list[str]
    card: str | None
    outcome: str | None
    reason: str | None
    created: str
    last_seen: str

    @classmethod
    def of(cls, observation: Observation) -> Snapshot:
        return cls.model_validate(observation.model_dump(mode="json", include=set(SNAPSHOT_KEYS)))


class Report(_Model):
    """A reaction on the observation; `new` when it came after the item's previous version."""

    at: str
    emoji: str
    repo: str
    session: str | None
    client: str | None
    text: str | None
    new: bool


class Ruling(_Model):
    """The operator's: a draft until `submitted` is set, the actioner's from then on."""

    verb: Verb
    note: str
    at: str
    submitted: str | None


class Question(_Model):
    """The actioner's, handing the item back to the operator."""

    at: str
    text: str


class Actioned(_Model):
    at: str
    done: str


class TriageItem(_Model):
    """A triage item, every field as the store holds it."""

    observation: str
    written: str
    headline: str
    ask: str
    evidence: str
    recommendation: str
    impact: str
    snapshot: Snapshot
    reports: list[Report]
    ruling: Ruling | None
    question: Question | None
    actioned: Actioned | None

    @property
    def submitted(self) -> bool:
        return self.ruling is not None and self.ruling.submitted is not None

    @property
    def returned(self) -> bool:
        """Handed back with a question and not submitted since."""
        return self.question is not None and not self.submitted

    @property
    def ruled(self) -> bool:
        """A ruling in force: on a returned item, one made after its question."""
        return self.ruling is not None and (
            self.question is None or self.ruling.at > self.question.at
        )


class TriageQueue(_Model):
    """The operator's queue: the items, and each item's observation as it stands now, or null
    for one the store no longer has."""

    items: list[TriageItem]
    observations: dict[str, Snapshot | None]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


Written = Annotated[str, Field(pattern=TIME_PATTERN)]


class RulingRequest(_Request):
    """A ruling, with the item's `written` as the operator's page saw it."""

    verb: Verb
    note: Annotated[str, StringConstraints(max_length=TEXT_MAX)]
    written: Written

    @model_validator(mode="after")
    def _noted(self) -> RulingRequest:
        if self.verb is not Verb.yes and not self.note:
            raise ValueError(
                f"a {self.verb} needs a note: it is what the actioner and the next reporter get"
            )
        return self


class TakeBackQuery(_Request):
    """A take-back's query: the item's `written` as the operator's page saw it."""

    written: Written


class SubmitReply(RootModel[list[str]]):
    """The ids of the items submitted; empty when nothing was ruled."""

    model_config = ConfigDict(frozen=True)


class ItemFault(ValueError):
    """What makes a file under `triage/` no open item the app can read."""


def parse_item(name: str, text: str) -> tuple[dict[str, Any], TriageItem]:
    """The open item `triage/<name>`: its JSON as read, and its model."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ItemFault(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ItemFault("not a JSON object")
    faults = item_faults(name, data)
    if faults:
        raise ItemFault("; ".join(faults))
    try:
        return data, TriageItem.model_validate(data)
    except ValidationError as exc:
        raise ItemFault(str(exc)) from exc


# -- the index -----------------------------------------------------------------------------------


class TriageIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._items: dict[str, TriageItem] = {}  # observation id -> its open item
        self._lock = threading.Lock()  # the store's listener writes on the queue's thread

    def items(self) -> list[TriageItem]:
        with self._lock:
            return list(self._items.values())

    def _read(self, path: str, name: str) -> TriageItem | None:
        try:
            _, item = parse_item(name, (self.root / path).read_text())
        except ItemFault as exc:
            logger.error("%s is left out of the triage queue: %s", path, exc)
            return None
        return item

    def update(self, paths: set[str]) -> None:
        """Take in the changed paths: a listener of the store."""
        read: dict[str, TriageItem | None] = {}
        for path in paths:
            match = _PATH.match(path)
            if match is None:
                continue
            id_ = match.group(1)
            read[id_] = self._read(path, f"{id_}.json") if (self.root / path).exists() else None
        with self._lock:
            for id_, item in read.items():
                if item is None:
                    self._items.pop(id_, None)
                else:
                    self._items[id_] = item

    def queue(self) -> list[TriageItem]:
        """FR-23: every item whose ruling is absent or not submitted; returned items first, then
        the rest, each group by `written`, oldest first."""
        waiting = [item for item in self.items() if not item.submitted]
        return sorted(
            waiting, key=lambda item: (not item.returned, item.written, item.observation)
        )


def queue_reply(triage: TriageIndex, index: Index) -> TriageQueue:
    items = triage.queue()
    observations: dict[str, Snapshot | None] = {}
    for item in items:
        entry = index.get(item.observation)
        observations[item.observation] = (
            Snapshot.of(entry.observation) if entry is not None else None
        )
    return TriageQueue(items=items, observations=observations)
