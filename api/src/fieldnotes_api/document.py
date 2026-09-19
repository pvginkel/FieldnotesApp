"""The observation file, `observations/<ulid>.md` (FR-8): parse, render, and edit in place.

A file is YAML frontmatter between `---` lines, then a body of two sections, `### reactions` and
`### comments`, each a YAML list of entries:

    ---
    id: 01K5H8ZQ3V6D9W2X4Y7B1C0E5F
    status: open
    area: uv workspace
    category: hint
    repos: [pvginkel/Example]
    created: 2026-09-19T10:04:12Z
    ...
    ---

    ### reactions

    - at: 2026-09-19T10:04:12Z
      emoji: 📝
      repo: pvginkel/Example
      client: mcp
      text: Sync with --all-packages.

    ### comments

Skills and people edit these files by hand, so the server never re-renders one. An edit replaces
the lines of each field it sets and nothing else, and a reaction is inserted after the last entry
of its section: whatever else a hand edit left in the file (comments, key order, block scalars,
fields the server does not know) survives a server write byte for byte.

Scalars are read as strings: the loader resolves `null` and nothing else, so a hand-written `card:
12` or `reason: no` reads as the text it looks like. The renderer quotes whatever a standard YAML
reader would take for a boolean or a number, and leaves timestamps plain.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import cached_property
from typing import Any

import yaml
from pydantic import ValidationError

from fieldnotes_contracts import Comment, Observation, Reaction

# FR-8's frontmatter fields, in its order: the order a new file is written in.
FIELDS = (
    "id",
    "status",
    "area",
    "category",
    "repos",
    "created",
    "last_updated",
    "last_reviewed",
    "last_seen",
    "canonical",
    "outcome",
    "reason",
    "card",
    "card_updated",
    "pointer",
)
_NULLABLE = frozenset({"last_reviewed", "outcome", "reason", "card", "card_updated", "pointer"})

REACTIONS = "reactions"
COMMENTS = "comments"

_FENCE = "---"
_HEADING = re.compile(r"^### (reactions|comments)[ \t]*$")
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]|$)")


class DocumentError(ValueError):
    """A file that is not a valid observation. The message names what is wrong with it."""


class _Loader(yaml.SafeLoader):
    """Every scalar a string, `null` excepted."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag == "tag:yaml.org,2002:null"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class _Dumper(yaml.SafeDumper):
    """Quotes what a standard reader would resolve to a non-string, timestamps excepted; writes
    a multi-line string as a literal block and a list on one line."""


_Dumper.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeDumper.yaml_implicit_resolvers.items()
}


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # The emitter falls back to a quoted style where a literal block cannot hold the value.
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


def _represent_list(dumper: yaml.SafeDumper, value: list[Any]) -> yaml.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=True)


_Dumper.add_representer(str, _represent_str)
_Dumper.add_representer(list, _represent_list)


def format_time(value: datetime) -> str:
    """The one spelling of a timestamp in a file: UTC, whole seconds, `Z`."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: Any) -> str:
    return yaml.dump(
        value,
        Dumper=_Dumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=float("inf"),
    )


def _plain(value: Any) -> Any:
    """A value as the file spells it: timestamps and enum members as strings, the rest as it
    is."""
    if isinstance(value, datetime):
        return format_time(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [_plain(item) for item in value]
    return value


def render_field(key: str, value: Any) -> list[str]:
    """The lines of one frontmatter field."""
    return _dump({key: _plain(value)}).splitlines()


def render_entry(entry: Mapping[str, Any]) -> list[str]:
    """The lines of one reaction or comment entry, `None` fields left out."""
    lines = _dump({k: _plain(v) for k, v in entry.items() if v is not None}).splitlines()
    return [f"- {lines[0]}", *(f"  {line}" for line in lines[1:])]


def _load(text: str, what: str) -> Any:
    try:
        return yaml.load(text, Loader=_Loader)  # noqa: S506 - _Loader is a SafeLoader
    except yaml.YAMLError as exc:
        raise DocumentError(f"the {what} is not valid YAML: {exc}") from exc


@dataclass(frozen=True)
class Document:
    """One observation file's text, and the edits the server makes to it."""

    text: str

    # -- structure -------------------------------------------------------------------------------

    @cached_property
    def _lines(self) -> list[str]:
        return self.text.split("\n")

    @cached_property
    def _closing(self) -> int:
        """The index of the line that closes the frontmatter."""
        lines = self._lines
        if not lines or lines[0].rstrip() != _FENCE:
            raise DocumentError("the file does not start with a `---` frontmatter line")
        for index in range(1, len(lines)):
            if lines[index].rstrip() == _FENCE:
                return index
        raise DocumentError("the frontmatter has no closing `---` line")

    def _field_spans(self) -> dict[str, tuple[int, int]]:
        """Each top-level frontmatter key's lines, `[start, end)`: its own line and the lines
        that continue its value. A column-0 comment line and the blank lines before the next key
        belong to no field."""
        spans: dict[str, tuple[int, int]] = {}
        closing = self._closing
        index = 1
        while index < closing:
            match = _KEY.match(self._lines[index])
            if not match:
                index += 1
                continue
            end = index + 1
            while (
                end < closing
                and not _KEY.match(self._lines[end])
                and not self._lines[end].startswith("#")
            ):
                end += 1
            last = end
            while last > index + 1 and not self._lines[last - 1].strip():
                last -= 1
            spans[match.group(1)] = (index, last)
            index = end
        return spans

    def _sections(self) -> dict[str, tuple[int, int]]:
        """Each body section's lines after its heading, `[start, end)`."""
        headings = [
            (match.group(1), index)
            for index in range(self._closing + 1, len(self._lines))
            if (match := _HEADING.match(self._lines[index]))
        ]
        sections: dict[str, tuple[int, int]] = {}
        for position, (name, index) in enumerate(headings):
            if name in sections:
                raise DocumentError(f"the body has two `### {name}` sections")
            end = headings[position + 1][1] if position + 1 < len(headings) else len(self._lines)
            sections[name] = (index + 1, end)
        if REACTIONS not in sections:
            raise DocumentError("the body has no `### reactions` section")
        return sections

    # -- reading ---------------------------------------------------------------------------------

    @cached_property
    def frontmatter(self) -> dict[str, Any]:
        data = _load("\n".join(self._lines[1 : self._closing]), "frontmatter")
        if not isinstance(data, dict):
            raise DocumentError("the frontmatter is not a mapping")
        return data

    def _entries(self, section: str) -> list[dict[str, Any]]:
        span = self._sections().get(section)
        if span is None:
            return []
        data = _load("\n".join(self._lines[span[0] : span[1]]), f"`### {section}` section")
        if data is None:
            return []
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise DocumentError(f"the `### {section}` section is not a list of entries")
        return data

    @cached_property
    def observation(self) -> Observation:
        """The file as the contract's observation. Frontmatter keys and entry keys the contract
        does not know are left in the file and out of this."""
        fields = {key: self.frontmatter.get(key) for key in FIELDS}
        missing = [key for key in FIELDS if key not in _NULLABLE and fields[key] is None]
        if missing:
            raise DocumentError(f"the frontmatter lacks {', '.join(missing)}")
        try:
            reactions = [_known(Reaction, entry) for entry in self._entries(REACTIONS)]
            comments = [_known(Comment, entry) for entry in self._entries(COMMENTS)]
            return Observation(**fields, reactions=reactions, comments=comments)
        except ValidationError as exc:
            raise DocumentError(f"the file does not validate: {_summary(exc)}") from exc

    # -- editing ---------------------------------------------------------------------------------

    def with_fields(self, **values: Any) -> Document:
        """This document with the given frontmatter fields set. A field already present has its
        lines replaced where they are; an absent one is added before the closing `---`."""
        lines = list(self._lines)
        spans = self._field_spans()
        closing = self._closing
        replacements = [
            (spans[key], render_field(key, value)) for key, value in values.items() if key in spans
        ]
        added = [
            line
            for key, value in values.items()
            if key not in spans
            for line in render_field(key, value)
        ]
        lines[closing:closing] = added
        for (start, end), rendered in sorted(replacements, reverse=True):
            lines[start:end] = rendered
        return Document("\n".join(lines))

    def with_reaction(self, reaction: Reaction) -> Document:
        """This document with the reaction entered after the last one (FR-4)."""
        lines = list(self._lines)
        start, end = self._sections()[REACTIONS]
        last = end
        while last > start and not lines[last - 1].strip():
            last -= 1
        entry = ["", *render_entry(reaction.model_dump())]
        if last == end and end < len(lines):
            entry.append("")
        lines[last:last] = entry
        return Document("\n".join(lines))


def _known(model: type[Reaction] | type[Comment], entry: dict[str, Any]) -> Any:
    return model(**{key: value for key, value in entry.items() if key in model.model_fields})


def _summary(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )


def new_document(
    *,
    id_: str,
    area: str,
    category: str,
    text: str,
    reaction: Reaction,
) -> Document:
    """The file a created post writes (FR-1, FR-8): status `open`, the post's text as the
    canonical statement, and the post itself as the first reaction."""
    fields: dict[str, Any] = {
        "id": id_,
        "status": "open",
        "area": area,
        "category": category,
        "repos": [reaction.repo],
        "created": reaction.at,
        "last_updated": reaction.at,
        "last_reviewed": None,
        "last_seen": reaction.at,
        "canonical": text,
        "outcome": None,
        "reason": None,
        "card": None,
        "card_updated": None,
        "pointer": None,
    }
    lines = [_FENCE]
    for key in FIELDS:
        lines += render_field(key, fields[key])
    lines += [_FENCE, "", f"### {REACTIONS}", "", *render_entry(reaction.model_dump())]
    lines += ["", f"### {COMMENTS}", ""]
    return Document("\n".join(lines))
