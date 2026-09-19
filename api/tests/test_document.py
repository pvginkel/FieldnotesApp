"""The observation file (FR-8): rendered as documented, read back exactly, and edited in place so
that a hand edit survives a server write."""

from datetime import UTC, datetime

import pytest

from fieldnotes_api.document import Document, DocumentError, new_document
from fieldnotes_contracts import Category, Reaction, Status

AT = datetime(2026, 9, 19, 10, 4, 12, tzinfo=UTC)
LATER = datetime(2026, 9, 20, 8, 0, 0, tzinfo=UTC)
ID = "01K5H8ZQ3V6D9W2X4Y7B1C0E5F"

NEW = f"""---
id: {ID}
status: open
area: uv workspace
category: hint
repos: [pvginkel/Example]
created: 2026-09-19T10:04:12Z
last_updated: 2026-09-19T10:04:12Z
last_reviewed: null
last_seen: 2026-09-19T10:04:12Z
canonical: 'uv sync: pass --all-packages.'
outcome: null
reason: null
card: null
card_updated: null
pointer: null
---

### reactions

- at: 2026-09-19T10:04:12Z
  emoji: 📝
  repo: pvginkel/Example
  session: s-1
  client: mcp
  text: 'uv sync: pass --all-packages.'

### comments
"""

# A file as a skill might leave it: keys reordered, a block scalar, a comment line, a key the
# server does not know, an extra key on a reaction, and a comment entry.
EDITED = f"""---
id: {ID}
area: uv workspace
status: raised
category: friction
# condensed by the reconciler
canonical: |
  A plain `uv sync` in a workspace whose root is not a package installs
  none of the members. Pass --all-packages.
repos:
- pvginkel/Example
- pvginkel/Other
created: 2026-09-19T10:04:12Z
last_updated: 2026-09-21T09:00:00Z
last_reviewed: 2026-09-21T09:00:00Z
last_seen: 2026-09-20T08:00:00Z
outcome:
reason: null
card: FN-12
card_updated: null
pointer: null
merged_from: [01K5H8ZQ3V6D9W2X4Y7B1C0E5G]
---

### reactions

- at: 2026-09-19T10:04:12Z
  emoji: 📝
  repo: pvginkel/Example
  text: 'uv sync: pass --all-packages.'
  merged: true

- at: 2026-09-20T08:00:00Z
  emoji: 👍
  repo: pvginkel/Other

### comments

- at: 2026-09-21T09:00:00Z
  author: reconciler
  text: Merged 01K5H8ZQ3V6D9W2X4Y7B1C0E5G into this one.
"""


def post_reaction(at=AT, **overrides):
    fields = {"at": at, "emoji": "📝", "repo": "pvginkel/Example", "session": "s-1"}
    fields |= {"client": "mcp", "text": "uv sync: pass --all-packages."}
    return Reaction(**{**fields, **overrides})


def new():
    return new_document(
        id_=ID,
        area="uv workspace",
        category="hint",
        text="uv sync: pass --all-packages.",
        reaction=post_reaction(),
    )


def test_a_new_file_is_rendered_as_documented():
    assert new().text == NEW


def test_a_new_file_reads_back():
    observation = Document(NEW).observation
    assert observation.id == ID
    assert observation.status is Status.open
    assert observation.category is Category.hint
    assert observation.repos == ["pvginkel/Example"]
    assert observation.created == AT
    assert observation.last_reviewed is None
    assert observation.canonical == "uv sync: pass --all-packages."
    assert observation.reactions == [post_reaction()]
    assert observation.comments == []


def test_a_hand_edited_file_reads():
    observation = Document(EDITED).observation
    assert observation.status is Status.raised
    assert observation.canonical.startswith("A plain `uv sync`")
    assert observation.canonical.endswith("Pass --all-packages.\n")
    assert observation.repos == ["pvginkel/Example", "pvginkel/Other"]
    assert observation.card == "FN-12"
    assert observation.outcome is None
    assert [r.emoji for r in observation.reactions] == ["📝", "👍"]
    assert observation.comments[0].author == "reconciler"


def test_setting_fields_changes_their_lines_and_nothing_else():
    edited = Document(EDITED).with_fields(
        status="closed", outcome="done", last_updated=LATER, pointer="pvginkel/Example#42"
    )
    expected = (
        EDITED.replace("status: raised", "status: closed")
        .replace("outcome:\n", "outcome: done\n")
        .replace("last_updated: 2026-09-21T09:00:00Z", "last_updated: 2026-09-20T08:00:00Z")
        .replace("pointer: null", "pointer: pvginkel/Example#42")
    )
    assert edited.text == expected


def test_setting_a_multi_line_field_replaces_all_of_its_lines():
    edited = Document(EDITED).with_fields(canonical="Pass --all-packages.", repos=["a/b"])
    assert "  none of the members" not in edited.text
    assert "- pvginkel/Other" not in edited.text
    assert "# condensed by the reconciler\ncanonical: Pass --all-packages.\nrepos: [a/b]\n" in (
        edited.text
    )
    assert edited.observation.canonical == "Pass --all-packages."
    assert edited.observation.repos == ["a/b"]


def test_setting_an_absent_field_adds_it_before_the_closing_line():
    text = NEW.replace("pointer: null\n", "")
    edited = Document(text).with_fields(pointer="x")
    assert edited.text == NEW.replace("pointer: null", "pointer: x")


def test_a_reaction_goes_after_the_last_one():
    reaction = Reaction(at=LATER, emoji="👎", repo="pvginkel/Third", text="Not any more.")
    edited = Document(EDITED).with_reaction(reaction)
    entry = (
        "- at: 2026-09-20T08:00:00Z\n  emoji: 👎\n  repo: pvginkel/Third\n  text: Not any more.\n"
    )
    assert edited.text == EDITED.replace(
        "  repo: pvginkel/Other\n\n### comments", f"  repo: pvginkel/Other\n\n{entry}\n### comments"
    )
    assert edited.observation.reactions[-1] == reaction


def test_a_reaction_goes_into_an_empty_section():
    text = "---\n" + NEW.split("---\n")[1] + "---\n\n### reactions\n### comments\n"
    edited = Document(text).with_reaction(post_reaction())
    assert "### reactions\n\n- at: 2026-09-19T10:04:12Z\n" in edited.text
    assert "text: 'uv sync: pass --all-packages.'\n\n### comments\n" in edited.text
    assert edited.observation.reactions == [post_reaction()]


def test_a_reaction_goes_at_the_end_of_a_file_without_comments():
    text = NEW.replace("\n### comments\n", "")
    edited = Document(text).with_reaction(post_reaction(at=LATER, emoji="👍", text=None))
    assert edited.text.endswith(
        "pass --all-packages.'\n\n- at: 2026-09-20T08:00:00Z\n"
        "  emoji: 👍\n  repo: pvginkel/Example\n  session: s-1\n"
        "  client: mcp\n"
    )
    assert len(edited.observation.reactions) == 2


@pytest.mark.parametrize(
    "value",
    [
        "no",
        "12",
        "yes: really",
        "null",
        "2026-09-19",
        "- a list?",
        "# not a comment",
        "line one\nline two",
        "trailing spaces   \nsecond line",
        "  leading indent\nsecond",
        "quotes ' and \" and \\ and tabs\t",
    ],
)
def test_any_text_survives_a_field_write(value):
    edited = new().with_fields(canonical=value, reason=value)
    assert edited.observation.canonical == value
    assert edited.observation.reason == value


def test_any_text_survives_a_reaction_write():
    text = "first\n\n  indented: yes\n- dash\ntrailing \n"
    edited = new().with_reaction(post_reaction(at=LATER, text=text))
    assert edited.observation.reactions[-1].text == text


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("no frontmatter\n", "does not start"),
        ("---\nid: x\n", "no closing"),
        (NEW.replace("### reactions", "### notes"), "no `### reactions`"),
        (NEW.replace("status: open", "status: pending"), "status"),
        (NEW.replace("category: hint", "category: bug"), "category"),
        (NEW.replace("created: 2026-09-19T10:04:12Z", "created: 2026-09-19T10:04:12"), "created"),
        (NEW.replace("area: uv workspace\n", ""), "lacks area"),
        (NEW.replace("### comments\n", "### comments\n\nfree text\n"), "not a list"),
        (NEW.replace("repos: [pvginkel/Example]", "repos: [unclosed"), "not valid YAML"),
        (NEW + "\n### reactions\n", "two `### reactions`"),
    ],
)
def test_an_invalid_file_is_refused_with_the_reason(text, complaint):
    with pytest.raises(DocumentError, match=complaint):
        _ = Document(text).observation
