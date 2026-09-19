# The observation file

This covers the storage layout of an observation: the file's path, its frontmatter fields, its body
sections, and the rules that make hand editing it safe. People and skills edit these files by hand
(FR-8), so the layout changes as deliberately as the [REST surface](rest-api.md) does; see
[change-discipline.md](change-discipline.md).

## Path

`observations/<ulid>.md` in the store repo. The frontmatter `id` must equal the file name's ULID.

## Full example

A file the API writes for a new post:

```
---
id: 01K5H8ZQ3V6D9W2X4Y7B1C0E5F
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
```

## Frontmatter fields

In the order a new file has them:

| Field | What | Written by the API |
| --- | --- | --- |
| `id` | the ULID, equal to the file name | on post |
| `status` | `open`, `proposed`, `raised` or `closed` | on post (`open`); by board sync (`closed`) |
| `area` | free text, what the observation is about | on post |
| `category` | `hint`, `idea` or `friction` | on post |
| `repos` | the repositories that posted or reacted, a list | on post and react |
| `created` | when it was posted | on post |
| `last_updated` | changes on any write to the file, by anyone; the reconciler's queue is `last_updated > last_reviewed` | on every write |
| `last_reviewed` | when the reconciler last reviewed it; null until then | never |
| `last_seen` | the latest post or reaction | on post and react |
| `canonical` | the statement; starts as the post's text | on post |
| `outcome` | `done` or `wont-do` once closed | by board sync |
| `reason` | why, for a `wont-do` ruling | never |
| `card` | the YouTrack issue's readable id, e.g. `FN-12` | never (the actioner sets it) |
| `card_updated` | the time of the card's latest change, comments included | by board sync |
| `pointer` | where the work landed, from a `Resolved:` comment on the card | by board sync |

`reason`, `outcome`, `card`, `card_updated`, `pointer` and `last_reviewed` may be null; the rest are
required. Timestamps are UTC, whole seconds, `YYYY-MM-DDTHH:MM:SSZ`; a timestamp without a zone is
refused.

## Body

The body holds two sections in this order: `### reactions` (required) and `### comments`, each a
YAML list of entries.

- A reaction entry has `at`, `emoji`, `repo`, and optionally `session`, `client` (the API client
  that wrote it; absent on an entry a skill wrote), `text`.
- A comment entry has `at`, `author`, `text`.

The creating post is the first reaction entry, with emoji `📝` and the post's text, so every report
has the same provenance shape and the reaction counts include the post. The API writes reactions,
never comments; comments are the skills'.

## Reading rules

These make hand editing safe:

- Scalars are read as strings; only `null` (and an empty value) is resolved. So `card: 12` or
  `reason: no` reads as the text it looks like, with no quoting needed. Block scalars (`|`, `>`) and
  multi-line lists are fine.
- Frontmatter keys and entry keys the API does not know stay in the file and are ignored by the API.
  A column-0 `#` comment line is fine.
- A multi-line string the API writes is a literal block; a list is written on one line; strings a
  standard YAML reader would take for a boolean or a number are quoted.

## How the API edits a file

The API never re-renders one. Setting a field replaces that field's lines where they are (an absent
field is added before the closing `---`); a reaction is inserted after the last reaction entry.
Everything else a hand edit left in the file (comments, key order, block scalars, unknown keys)
survives an API write byte for byte.

## Files that do not parse

A file that does not parse as an observation (missing a required field, an unknown status or
category, a naive timestamp, a section that is not a list, broken YAML, or an `id` that is not its
file name) is logged and left out of the index until an edit fixes it: meanwhile `GET` and a
reaction answer 404 for it. A reaction that finds the file broken when it comes to write, before the
index has caught up with the edit that broke it, answers 409 with the fault named.

## What is matched

Only `area + ": " + canonical` is embedded and matched. Reactions and comments are never embedded,
so they never change what matches; a rewritten canonical does. See
[match-pipeline.md](match-pipeline.md) for how that text is used.

Skills edit these files directly and push; the API takes their edits in through the
[GitHub webhook](webhooks.md), or at the latest with its next write, which always fetches first.
