# Webhooks and board sync

This covers the two inbound webhooks, GitHub's push notifications and YouTrack's board events, and
board sync, the routine that reconciles an observation with its YouTrack card (FR-20..FR-22).

## GitHub: `POST /hooks/github`

The store repo's webhook reaches the API through the homelab's `webhook-relay`, the only
internet-facing container, which verifies the signature and forwards the raw delivery with its
signature headers. The API verifies again, over the exact bytes received, before anything else:
`X-Hub-Signature-256` must be `sha256=` plus the hex HMAC-SHA256 of the body under
`FIELDNOTES_GITHUB_WEBHOOK_SECRET`. The webhook must deliver `application/json`.

A `push` event (`X-GitHub-Event: push`) whose `repository.full_name` equals
`FIELDNOTES_GITHUB_REPO` (compared without case) and whose `ref` is
`refs/heads/<FIELDNOTES_STORE_BRANCH>` queues a pull and answers `{"action": "queued"}` at once;
nothing is pulled before the answer, because the relay gives each receiver four seconds. `ping`,
every other event, branch and repository answer 200 `{"action": "ignored"}`.

A bad or missing signature is a 401, and nothing is pulled. The secret and the repo are set together
or not at all; without them every delivery is refused. The API's own pushes come back as deliveries
and cost one fetch that finds nothing new.

The pull runs in the same queue as the writes described in [rest-api.md](rest-api.md) and brings the
index up to the new commit; see [match-pipeline.md](match-pipeline.md) for how the index re-reads
only what changed, re-embedding only a changed canonical.

## YouTrack: `POST /hooks/youtrack`

The board's events come from JetBrains' Webhook Triggers app, configured per YouTrack project,
in-cluster with no relay. Point the app at the endpoint for issue updates and for comments added,
updated and deleted.

The app sends a shared token of at least 32 characters in a header it lets you name; the API
compares it, in constant time, with `FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN`, in the header named by
`FIELDNOTES_YOUTRACK_WEBHOOK_HEADER` (default `X-YouTrack-Token`, the app's own default).

Of the JSON payload the API reads only `id`, the issue's readable id (e.g. `FN-12`). An issue that
is no observation's `card` (compared without case) is answered `{"action": "ignored"}` without a
call to YouTrack; most board events are of that kind. For a card it queues a board sync of that
observation and answers `{"action": "queued"}`: the app sends its webhooks one after another and
waits for each answer, at most five seconds, so nothing is read or written before the answer.

**The card is read `FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE` seconds after the delivery** (default 5).
YouTrack sends a delivery before it commits the change the delivery is about, so a card read at once
is the card as it was one event earlier: seen live on 2026-09-21, where the sync a comment's delivery
queued read the card within 600 ms and did not find the comment, and the next event's sync recorded
it. A delivery that arrives while an earlier one for the same observation is still waiting restarts
the wait, so a burst of events costs one read. The wait is not spent in the write queue. A change the
wait still misses is picked up by the reconciler's board scan.

A payload with no string `id`, or that is not JSON, is ignored. A token that does not verify is a
401; without the token configured every delivery is refused; before the index is built the answer
is `503 not-ready`.

## Board sync

One routine behind the YouTrack webhook and `POST /observations/{id}/board-sync` (see
[rest-api.md](rest-api.md)), which the reconciler's board scan calls for every observation with a
card to cover missed webhooks. The result never depends on which event arrived or in what order,
because it reads the card whole:

```
GET /api/issues/<card>?fields=idReadable,updated,customFields(name,value(name)),comments(text,created,updated,deleted)
```

on `FIELDNOTES_YOUTRACK_URL`, with `FIELDNOTES_YOUTRACK_TOKEN` (read-only) as a bearer. From it:

- **the resolution**: the value of the field named by `FIELDNOTES_YOUTRACK_RESOLUTION_FIELD`
  (default `Resolution`). YouTrack leaves that field out of `customFields` altogether while its
  condition hides it (an issue not in Done), and an absent field reads as unset;
- **the card's latest change**: the later of the issue's `updated` and every non-deleted comment's
  created and updated times, cut to whole seconds, so a comment counts whether or not it moves the
  issue's own `updated`;
- **the pointer**: from the newest non-deleted comment with a line starting `Resolved:`, the rest of
  that line.

It writes only when the card's latest change is later than the observation's `card_updated` (or
`card_updated` is null; see [observation-file.md](observation-file.md) for that field). Then:

- `card_updated` takes that time and `last_updated` moves to now, which puts the observation in the
  reconciler's queue (FR-22), a comment included;
- if the resolution maps to an outcome through `FIELDNOTES_YOUTRACK_OUTCOMES` (default
  `Resolved=done,Absorbed=done,Won't Do=wont-do`, as `<value>=<outcome>` pairs separated by commas)
  and the observation is `raised` or `closed`, it becomes `closed` with that outcome, so a later
  change to the field changes the outcome. An `open` or `proposed` observation that still has a card
  was reopened: its old card's resolution is history, and its status stays;
- a pointer found on the card replaces a different one.

A resolution that maps to nothing, or has been cleared, changes neither status nor outcome; the
change still queues the observation, and the reconciler reads what changed.

When the card has not changed, nothing is written and the reply says `changed: false`, so a rescan
dirties no observation. The link is one-way: the observation records the issue in `card`, and the
board carries nothing of Fieldnotes'.

## Refusals

An observation with no card, or a card YouTrack does not have, is a 409 `conflict`; YouTrack
unreachable or refusing the read is a 502 `board-unreachable`; with `FIELDNOTES_YOUTRACK_URL` and
`FIELDNOTES_YOUTRACK_TOKEN` unset (they are set together or not at all) a sync is a 503
`board-unreachable`. A sync queued by the webhook that fails is logged.
