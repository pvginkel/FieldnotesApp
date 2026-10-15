# The REST API

This covers the HTTP surface the backend exposes: authentication, every endpoint and request
model, what each does, error shapes, startup behaviour and settings.

## Listening and authentication

The surface is the ModernAppTemplate backend's (`backend/`, Flask under waitress): it listens on
`HOST`:`PORT` (default `0.0.0.0:3401`), the Fieldnotes endpoints live under `/api`
(`backend/app/api/fieldnotes.py`), and the domain behind them is `backend/app/fieldnotes/`. The
UI's nginx in the same pod proxies `/api/` to it.

Every endpoint except the health checks, `GET /metrics`, the three webhooks and the triage and inbox
endpoints requires `Authorization: Bearer <token>` (NFR-4). These are `@public` to the template's
OIDC hook, which gates only the UI's own endpoints on the operator's session: the agents' surface
checks its client tokens itself. A token resolves to a named client configured as
`FIELDNOTES_CLIENT_TOKEN_<NAME>`, the name being the suffix lowercased (`mcp`, `skills`). Resolution
compares digests in constant time against every configured client. With no client configured, every
bearer is refused. The resolved client's name goes into the commit message of any write, onto the
reactions it writes (`client`), and into the log. The webhooks verify their own secrets, but for
KubeCoder's, which carries none and is acted on only for the run the actioner holds; see
[webhooks.md](webhooks.md).

The triage endpoints (`/api/triage/…`, `backend/app/api/triage.py`) and the inbox endpoints
(`/api/inbox…`, `backend/app/api/inbox.py`) are the operator's UI's, and take the operator's OIDC
session instead (NFR-4): the template's, gated on the `editor` client role for every method, a GET
included. A request with no session, or with an agent's bearer token, is
refused with 401, and a signed-in user without the role with 403, both in the template's own
`{error}` body rather than problem+json.

The template's OIDC hook on the `/api` blueprint also binds the page's SSE connection (NFR-4).
Every request the UI's client makes names its page's connection in the `X-SSE-Request-Id` header
once the page has one, and a request that authenticated as a user, refused or not (a viewer's 403
binds the viewer), binds the connection the header names to that user when this pod holds it bound
to no one. The gateway's connect binds a connection from the access cookie, and with OIDC off to
`local-user`; a page whose connect came before its token's refresh, or that reconnected after a
rollout, is bound by its next request. A bound connection is never rebound. A request that
authenticates as no one binds nothing: a 401, a `@public` endpoint such as `GET /api/auth/self`,
and any path outside the `/api` blueprint, the gateway's callback and `/api/testing/…` among them.

## Endpoints

The `{id}` path segment must be a ULID (26 characters of Crockford base32,
`0-9A-HJKMNP-TV-Z`); anything else is a 422.

| Method, path | Request | Success |
| --- | --- | --- |
| `POST /api/observations` | `PostRequest` | 201 `{id, candidates: []}` when created; 200 `{id: null, candidates: [...]}` when candidates came back and nothing was created |
| `POST /api/observations/{id}/reactions` | `ReactRequest` | 200 `{id, reactions}` |
| `GET /api/observations/{id}` | | 200 the full observation |
| `GET /api/observations/{id}/neighbors?k=5` | `k` 1–12, default 5 | 200 `{id, neighbors: [candidate]}` |
| `POST /api/observations/{id}/board-sync` | | 200 `BoardSyncReply` |
| `POST /api/match` | `MatchRequest` | 200 `{candidates, indexed_commit}` |
| `POST /api/hooks/github`, `POST /api/hooks/youtrack` | the sender's payload | 200 `{action: "queued" or "ignored"}` |
| `POST /api/hooks/kubecoder` | the controller's prompt-run outcome | 200 `{action: "queued" or "ignored"}` |
| `GET /api/triage/queue` | | 200 `{items, observations}` |
| `PUT /api/triage/items/{id}/ruling` | `RulingRequest` | 200 the item as it now stands |
| `DELETE /api/triage/items/{id}/ruling?written=…` | `written` | 200 the item as it now stands |
| `POST /api/triage/submit` | | 200 the ids submitted, a list, empty when nothing was ruled |
| `GET /api/triage/actioner` | | 200 `{state, run, since, lines, failure}` |
| `POST /api/triage/actioner/clear` | `ActionerFailure` | 200 the actioner's state as it then stands |
| `GET /api/inbox` | | 200 `{configured, tracker, sitting}`, the sitting null when none is held |
| `POST /api/inbox/start` | `StartRequest` | 202 the inbox's state with the sitting begun; 200 with the live sitting, starting nothing |
| `POST /api/inbox/turns` | `TurnRequest` | 202 the inbox's state, the turn in flight in it |
| `POST /api/inbox/end` | | 204, also when no sitting is held |
| `GET /health/healthz`, `GET /health/readyz` | | the template's health checks; readiness carries a `store` entry, `{ok, failed}` |
| `GET /metrics` | | 200 the Prometheus text exposition |

Request models strip surrounding whitespace from strings; a blank required string is refused;
unknown fields are refused.

- `PostRequest`: `area` (1–200 chars, free text), `category` (`hint`, `idea` or `friction`; there is
  no `bug`: product bugs are not observations), `text` (1–10,000), `repo` (1–200; the repository, a
  path only where no repository applies), `session` (optional, ≤200, provenance), `force` (bool,
  default false).
- `ReactRequest`: `emoji` (1–32 chars, uncurated, 👎 allowed), `text` (optional, ≤10,000: only what
  is new), `repo`, `session` (optional).
- `MatchRequest`: `text`, `area` (optional; when given, the query is `area: text`, the same shape as
  an observation's embedded text), `k` (1–12, default 3).
- `RulingRequest`: `verb` (`yes`, `no` or `later`), `note` (≤10,000; blank is allowed on a `yes`
  only), `written` (the item's `written` as the operator's page saw it, `YYYY-MM-DDTHH:MM:SSZ`). The
  take-back's `written` query parameter is the same.
- `StartRequest`: `scope` (optional, ≤500, appended to the inbox prompt; empty for the whole
  inbox).
- `TurnRequest`: `message` (1–20,000, the operator's words).
- `ActionerFailure`, the clear's body, is the one exception to the rules above: the failure as
  `GET /api/triage/actioner` answered it, `at`, `outcome` and `reason`, each required, `reason`
  sent as null when it was null. It is compared as sent, not stripped, and fields beyond the three
  are ignored.

## What each endpoint does

- **Post** (FR-1..FR-3): unless `force`, runs the match pipeline on `area: text`; if any candidate
  clears the related threshold, returns at most 3 of them and creates nothing. Otherwise (or with
  `force`) creates `observations/<ulid>.md` with status `open`, the text as the canonical statement,
  and the post itself as the first reaction (emoji 📝, carrying the post's text, repo, session and
  client). Closed observations match too. See [match-pipeline.md](match-pipeline.md) for how
  candidates are found and [observation-file.md](observation-file.md) for the file it writes.
- **React** (FR-4): appends a reaction with `at`, `emoji`, `repo`, `session`, `client`, `text`; sets
  `last_seen` and `last_updated` to now; adds the repo to `repos` if new. Allowed on closed
  observations (the reconciler reads that as a re-raise). The reply's `reactions` are the counts
  after the reaction.
- **Get** (FR-5): the full observation from the index: every frontmatter field plus `reactions` and
  `comments`.
- **Match**: the post's pipeline, writing nothing. `indexed_commit` is the store commit the index
  had taken in when the match ran (null until it has taken one in): the match reflects that commit
  or a later one. A skill that pushed compares it with its own `HEAD` to tell whether its push was
  scored yet. A pod's index takes a push in with the GitHub webhook's pull when the delivery reached
  that pod, and otherwise with the pod's next write or timed pull, within about a minute (see
  [webhooks.md](webhooks.md)); and only once the models pod answers (see
  [match-pipeline.md](match-pipeline.md#the-index)).
- **Neighbors**: the observations nearest this one by the pipeline's score, with no threshold or gap
  cut;
  `match_class` is null for a neighbour below both thresholds; the observation itself is left out.
  For the reconciler's search for missed duplicates.
- **Board sync**: reconciles an observation's card with YouTrack. See
  [webhooks.md](webhooks.md#board-sync).
- **Triage queue** (FR-23): the operator's queue. `items` holds every open item in the store's
  `triage/` (not `done/`, not `archive/`) whose ruling is absent or not submitted, every field as
  the item's file holds it, but for a ruling from before `guessed`, which is served with
  `guessed: false`. Returned items (a `question`, and not submitted since) come first, then
  the rest, each group by `written`, oldest first. `observations` maps each item's observation id to
  its fields as the observation index holds them, which is as they stand now unless the index lags
  (see [match-pipeline.md](match-pipeline.md#the-index)): the ten fields of an item's `snapshot`,
  spelled as the snapshot spells them, so an observation unchanged since its item was written equals
  the snapshot. It is null for an observation the index does not hold (merged away or deleted, or
  not taken in yet). The items come from the
  triage index, which reads `triage/*.json` at startup and after every pull and write. An item file
  that is not JSON, breaks the store's item rules (the app's own copy of the store's check) or does
  not parse is logged and left out.
- **Ruling** (FR-25): sets the item's `ruling` to
  `{verb, note, at: now, submitted: null, guessed: false}` over any draft, the reconciler's guessed
  one included. A returned item keeps its `question`; a ruling whose `at` is later than the
  question's is its answer. A `no` or a `later` without a note is refused (`validation-error`).
- **Take-back** (FR-25): sets the item's `ruling` to null. On an item without a ruling it writes
  nothing.
- **Refusals of a ruling and a take-back**, each decided on the item as the store holds it at the
  tip the write lands on, never on the triage index, which can trail a skill's push: `not-found`
  when `triage/` has no such item (the actioner stamped it or the reconciler withdrew it);
  `conflict` when the item is submitted (the actioner's), when its `written` is not the one sent
  (the reconciler rewrote it after the page loaded, and the ruling is dropped), or when its file is
  one the queue leaves out (not JSON, against the store's item rules, or not parsing), the fault
  named in the detail. A refusal writes nothing.
- **Submit** (FR-26): marks every ruled, unsubmitted item in `triage/` submitted (`ruling.submitted:
  now`) and answers with their ids. A guessed draft (the reconciler's, untouched) is ruled, and
  keeps `guessed: true`. A returned item counts as ruled only once its ruling's `at` is later than
  its question's. With nothing ruled it writes nothing, starts nothing and answers `[]`. A submit
  that submitted something then starts the actioner, off the request: see
  [The actioner start](#the-actioner-start).
- **Actioner state** (FR-33): the actioner as the app bar's Actioner pill and the actioner page
  show it: a start pending, the run held with its last log lines, and the last failure. See
  [The actioner's state](#the-actioners-state).
- **Clear** (FR-33): the editor's clear of the last failure, the one their page showed. See
  [The actioner's state](#the-actioners-state).
- **Inbox** (FR-28 to FR-32): the operator's inbox sitting, a headless KubeCoder session the API
  relays: its state, Start, a turn and End. See [The inbox sitting](#the-inbox-sitting).
- **Metrics**: counts for Prometheus, which scrapes the API through the Service's `prometheus.io/*`
  annotations. They hold counts and repo names only, never text. Grafana's "Fieldnotes" dashboard
  reads them. See [Metrics](#metrics).

## Metrics

The counters are held in memory and start again at zero when the pod restarts. `increase()` over a
range gets past a restart. Every counter label comes from a closed set, and every combination
exists at 0 from startup. Otherwise a series would first appear at 1, and `increase()` would miss
that first increment, which at this volume would be most of them. The repo and the emoji are
open-ended, so they come from the store gauges instead, which are read from the index at scrape
time.

| Metric | Labels | What it counts |
| --- | --- | --- |
| `fieldnotes_observations` | `status`, `category` | observations in the store (gauge) |
| `fieldnotes_store_reactions` | `repo`, `emoji` | reactions on the observations in the store; a post is its 📝 (gauge) |
| `fieldnotes_triage_queue` | | items in the operator's triage queue: open items whose ruling is absent or not submitted (gauge) |
| `fieldnotes_posts_total` | `client`, `category`, `outcome` | posts: `created` (nothing matched), `matched` (candidates returned, nothing created), `forced` |
| `fieldnotes_post_candidates_total` | `match_class` | candidates returned to matched posts |
| `fieldnotes_post_top_score` | | the best candidate's score on a matched post (histogram) |
| `fieldnotes_match_follow_ups_total` | `result` | what the reporter did after a matched post (below) |
| `fieldnotes_reactions_total` | `client` | reactions |
| `fieldnotes_gets_total` | `client` | observations read by id |
| `fieldnotes_webhook_deliveries_total` | `source`, `action` | verified deliveries, `queued` or `ignored`; a refused one shows only as a 401 in the request metric |
| `fieldnotes_board_syncs_total` | `result` | `changed`, `unchanged`, or `failed` (a queued sync that raised) |
| `fieldnotes_triage_rulings_total` | `verb` | the operator's rulings, `yes`, `no` or `later`; a refused one is not counted |
| `fieldnotes_actioner_starts_total` | `result` | actioner starts: `started` (the controller accepted the prompt run), `in_flight` (a run is held, tried again a minute later; no call) or `failed` (the controller refused the run or could not be reached, not retried); a start skipped for want of settings, or with nothing waiting, is not counted |
| `fieldnotes_actioner_runs_total` | `outcome`, `reason` | actioner runs ended, as the controller's webhook reports them: `success` (reason `none`), or `skipped` or `failed` with the controller's reason (`in-use`, `no-capacity`, `turn-error`, `timeout`, …) |
| `fieldnotes_http_request_duration_seconds` | `method`, `route`, `status` | requests by route template (histogram) |

A matched post is remembered for its reporter, meaning the `(repo, session)` it came from or the repo
alone when no session was passed, for 30 minutes. The reporter's next move settles it as one
follow-up:

- `reacted`: it reacted to one of the candidates it was offered, so the answer landed.
- `reacted_other`: it reacted to an observation it was not offered.
- `forced`: it posted with `force`, so no candidate was the thing.
- `reposted`: it posted again and matched again.
- `abandoned`: it did nothing within the window.

## Candidate

The element of `candidates` and `neighbors` (FR-2): `id`, `area`, `canonical`, `status`, `outcome`,
`pointer`, `reason` (what the operator ruled or the board decided, null until someone wrote one;
returned for the reporting agent to act on, never matched), `reactions` (a list of `emoji (n)` strings, most frequent first, ties in order of first
appearance; the creating post counts as `📝`), `cosine` (4 decimals), `score` (what the thresholds
read, 4 decimals: the cosine, plus the lexical overlap where the API weighs it in), `match_class` (`likely`, `related`, or null), `next_step`
(literal text for the reporting agent). For an id `X` the next_step text is exactly:

```
react(id="X", emoji, text?, repo, session?) if this is the observation you were posting, with text
only for what it does not already say; if no candidate is, post again with force=true
```

`BoardSyncReply`: `id`, `card`, `changed` (false when the card had not changed and nothing was
written), `status`, `outcome`, `pointer`, `card_updated`.

## Writes

Every write goes through the write queue of the pod that takes it, is a commit on the store's
`main`, and is pushed before the reply, so a 2xx means it is on the remote, whether or not the
observation index has taken it in: a write does not wait on the models pod (see
[match-pipeline.md](match-pipeline.md#the-index)). Commit messages: `post <id> (<client>): <area>`,
`post <id> (<client>, forced): <area>` for a post with `force`, `react <id> <emoji> (<client>)`,
`board-sync <id> <card>`, and the operator's `rule <id> <verb> (operator)`,
`unrule <id> (operator)`, `submit <n> (operator)`. A write fetches and resets to the
remote first, and a fetch that fails fails the write; a push rejected because another writer pushed
in between (a skill, or the other pod while a rollout runs two) makes the write start over on the
new tip and apply its edit again; a write that fails leaves nothing behind that a later push could
carry. A write that changes nothing commits nothing. The operator's writes write an item back as
it was read, two-space indented, UTF-8 unescaped, with a trailing newline, and change nothing but
its `ruling`, so a commit's diff is the ruling; timestamps are UTC, whole seconds, `Z`.

## The actioner start

A submit that submitted something starts the actioner (FR-27): the API asks the KubeCoder
controller for a prompt run, `POST /prompt-runs` on `FIELDNOTES_KUBECODER_URL` with
`FIELDNOTES_KUBECODER_TOKEN` as a bearer, of the actioner's prompt in the project
`FIELDNOTES_KUBECODER_REPO`, with `FIELDNOTES_KUBECODER_WEBHOOK_URL` as the run's webhook. The call
runs as a task on the template's task service, after the submit's write, so the reply does not wait
for it.

Each attempt first reads the triage index for the items that wait: submitted, and so not yet
stamped `actioned` (an open item that carries the stamp breaks the store's rules and is not in the
index). With none waiting it makes no call. It then reads the hold: `actioner-run.json` in
`FIELDNOTES_CACHE_DIR`, on the volume both pods mount, naming the run the API started last and when.
A live hold means a run is in flight, and the attempt is tried again 60 seconds later, and again
after that for as long as items wait. A hold 30 minutes old has expired, since its webhook will not
come, and is logged at WARNING and passed over. The controller's `202` and its `runId` start the
run, and the API takes the hold for it. Any other answer, a refused token included, or a
controller that cannot be reached is logged at ERROR, recorded as the actioner's last failure, and
not retried. Each call, and each attempt that found a live hold, is counted in
`fieldnotes_actioner_starts_total`.

The run's outcome comes back to `POST /api/hooks/kubecoder` (see
[webhooks.md](webhooks.md#kubecoder-post-apihookskubecoder)), which releases the hold:

- **a success** starts the actioner again, which calls nothing unless items still wait, such as an
  item submitted while the run worked;
- **a skip for want of an environment**, `in-use` (every environment of the project busy) or
  `no-capacity` (none could be started), is tried again 300 seconds later, while items wait;
- **any other ending**, a failure or a `no-environment` skip, is logged at ERROR with its reason and
  the tail of the session's answer, and not retried: the next submit, or a session in the store's
  environment asked to action the triage, is the retry.

Each is counted in `fieldnotes_actioner_runs_total`, and a skip or a failure, a retried skip
included, is recorded as the actioner's last failure.

At most one start is pending in a pod, from the submit that asked for it until the run starts, a
call fails or nothing waits; a submit through that pod meanwhile starts nothing, since the pending
start covers it. The pending start lives in the process alone: shutdown cancels a retry that waits,
a retired pod's included, and a pod that starts resumes none, so the items wait for the next submit
or the held run's webhook. The hold outlives the pod, and either pod may receive the webhook. Without
the four settings a submit still writes, and the start is skipped with a warning in the log.

Every write to the hold's file, a start's, the webhook's, the follow's and the clear's, loads and
writes it under an exclusive `flock` of `.actioner-run.json.lock` beside it, so no writer on either
pod loses another's write. A write that changes nothing writes nothing.

### The run's follow

The controller names no run's session, so the pod that starts a run finds it by elimination and
follows it, on a thread of its own, for the actioner page (FR-27). Before it asks for the run it
notes the headless sessions working in the project's running environments. Once the run is held it
looks every 5 seconds for a headless session that is working and is neither in the note nor the
live inbox sitting's own, and reads that session's response stream from its start. Each condensed
log line is kept in the hold's file, the last four as the run's `lines`, with the stream's offset
after it. The follow ends at the stream's result, at the session gone (`404` or `409`), or once
the hold is released or has expired; a stream that drops is read again from its offset after 2
seconds, the wait doubling up to 60. A note the controller cannot give leaves the run started and
not followed, with no lines. The follow decides nothing: what it sees or fails at changes none of
the run's start, its hold, its retries or its failure, and the webhook alone ends the run.

The hold's file records the follow beside the lines (`"lines"` and `"follow"` beside `"runId"`):
the note, the run's session once found, the offset, and the follow holding it, its pod, its id and
a heartbeat it refreshes every 15 seconds. Each write the follow makes is checked against the run
and that follow. A follow held by no follow, or whose heartbeat is a minute old, is taken over by
whichever pod next answers `GET /api/triage/actioner` or the clear, and goes on from the record:
the stream from the offset, or the look past the note while no session is recorded. A pod lets go
of its follow as its shutdown is announced (the chart's preStop drain), held by no follow, so the
other pod's next read takes it over at once; from then on it takes none over, quietly, and a run it
starts is held with no follow holding it, for the other pod. The result, or the session gone,
writes the follow out of the record, and no read takes it over then. A hold with no `"lines"`, as
a pod of an earlier release writes it during a rollout, loads with none.

### The actioner's state

`GET /api/triage/actioner` answers what the app bar's Actioner pill and the actioner page show
(FR-33), a takeover of the run's follow made first when one is due (see
[The run's follow](#the-runs-follow)):

- `state`: `running` while a live hold names a run, with `run` its id and `since` when the API
  started it; `starting` while the answering pod has a start pending, a call under way or a retry
  waiting, a retried skip's included; `idle` otherwise. A hold 30 minutes old counts as no run
  held. A pending start is the answering pod's alone, so through a rollout's overlap the other pod
  answers `idle` for it.
- `lines`: while `running`, the run's last condensed log lines, oldest first, up to four, `[]`
  until the follow has read one; `[]` otherwise.
- `failure`: the last failure, `{at, outcome, reason}`, or null. A run's comes from its webhook,
  `outcome` `failed` or `skipped` with the controller's `reason` (null when it gave none) and `at`
  the API's clock at the delivery. A start the controller refused or could not be reached is
  `refused`, its `reason` the controller's problem title, else `/prompt-runs answered <status>`, or
  the transport error. It is kept in the hold's file, `"failure"` beside `"runId"` and `"at"`, so
  both pods answer it, until the next run started clears it in the same write that takes the hold,
  or an editor clears it.

`POST /api/triage/actioner/clear` is that clear: its body names the failure as the editor's page
read it, and the failure is cleared only while it is the last failure, all three fields equal. A
newer failure, recorded since the page read it, stays, and so does the hold, a run held since
included. The reply is the state as it then stands, cleared or not, `failure` null once cleared or
the newer one; the log says which. A body without one of the three fields is `422
validation-error`, and nothing is cleared.

The times are in the store's spelling, `YYYY-MM-DDTHH:MM:SSZ`.

## The inbox sitting

The inbox sitting is the `dev` plugin's `/dev:inbox` skill in a KubeCoder headless session in an
environment of the store's project, held by the operator from the app's inbox screen (FR-28 to
FR-32). The API is its relay: it picks or starts the environment, creates the session, sends each
turn, follows the session's response stream and ends the session, through the controller at
`FIELDNOTES_KUBECODER_URL` with the actioner's token and project. The sitting needs those three
settings and not the webhook's; without them `GET` answers `configured: false` and Start and a turn
are refused (`conflict`).

`GET /api/inbox` answers `configured`, `tracker` (the issue address a card id is appended to, for
the screen's links) and `sitting`, the one sitting the API holds, or null:

- `environment`: the environment the sitting runs in, null until Start has picked one;
  `started_environment`: whether the API started it.
- `began`, and `moved`: the last send or result.
- `turn`: the turn in flight, a Start's included, or null: `task`, the id of the task-service task
  the page follows for it; `began`; and `lines`, the last four of its progress texts (below), a
  Start's steps and the condensed log lines read, oldest first, `[]` as the turn begins. No
  message of the turn is on it: the messages land whole, as its reply.
- `lost`: `{at, reason}` once the sitting is lost, else null.
- `ended`: `{at, idle_minutes}` once the idle expiry has ended the sitting, else null: when the
  sweep ended it, and the configured idle time in minutes.
- `transcript`: every entry, `{author, messages, at}`, `author` being `operator` or `session` and
  `messages` never empty. An operator's entry has one message, what the API sends the session: the
  first entry is the prompt, each later one the operator's words for a turn, `at` the time of the
  Start or the turn. A session's entry is its reply to a turn (below), `at` the time of the turn's
  result.

**Start** with a live sitting, neither lost nor ended, answers it, `200`, and starts nothing; a
lost or ended one it clears first, as End does. Otherwise it writes a new sitting whose first
message is `FIELDNOTES_KUBECODER_INBOX_PROMPT` (`/dev:inbox`) with the operator's scope appended
after a space, answers `202`, and runs a task that lists the project's environments in `normal`
mode and takes a running one, else starts a stopped one, records that the API started it, and
waits up to 15 minutes for it to settle. In it, the task creates a headless session in
`FIELDNOTES_KUBECODER_INBOX_CWD` (`Fieldnotes/inbox`, under `/work`, the store's `inbox/`) with the
configured model and reasoning effort, sends the first message, and follows the turn. The task's
progress texts are its steps (`Picking an environment of <project>`, `Taking <env>, which is
running` or `Starting <env>, which was stopped`, `Creating the session`), then the turn's log
lines.

**A turn** appends the operator's message and answers `202`, the turn in flight in the sitting; its
task sends the message and follows it. While a turn is in flight, a Start's included, a turn is
refused with `session-busy`, and without a live sitting, none, a lost one or an ended one, with
`conflict`.

**Following** a turn reads the session's response stream from the log offset the sitting last
read, past log lines and messages alike. Each condensed log line becomes the task's progress text
and the latest of the turn's `lines`, the oldest dropped past four. Each message of the turn,
whole, is kept on file with the turn in flight, and is neither a progress text nor a line; a
message of another turn, which a stream opened at an offset can replay, moves the offset alone. At
the turn's result the session's reply is appended to the transcript, which clears the turn: every
message the stream carried for the turn, final or not, in stream order, one of the entry's
`messages` each. The result's text is KubeCoder's join of the turn's final messages, with blank
lines, and is not shown again: it follows the messages only where it is not empty and differs from
that join, so it is the reply of a turn whose stream carried no message and follows the messages
of a turn that ends in an error no message carried. A turn with neither has one empty message. The
task completes with `{reply}`, the transcript's new entry, null when the sitting was ended, lost or
taken over under it.

**The page's events.** A task's events carry the session's words, so they reach only the SSE
connections bound to the editor whose request began the turn, a takeover's task's included (with
OIDC off, every connection). How a page's connection comes to be bound is under
[Listening and authentication](#listening-and-authentication). A page that hears nothing still
gets the reply by fetching `GET /api/inbox` again.

**Both pods.** The sitting is `inbox-sitting.json` in `FIELDNOTES_CACHE_DIR`, beside the actioner's
hold, written whole through a rename. Its writers, on either pod, take turns on an exclusive
`flock` of a lock file beside it, and each write a task makes is checked against the sitting it was
made for, so a task never brings back a sitting End cleared or writes into a newer one. Beyond the
wire, the file holds its status (`starting`, `busy`, `idle`, `lost` or `ended`), the subject of the
editor whose request began the last turn, the session's name, the conversation id once known, the
log offset after the last log line or message read, the number of the session's last turn whose
reply the transcript holds, whether the expiry has closed it, the messages of the turn in flight
read so far, and the follow holding that turn: its pod and a heartbeat it refreshes every 15
seconds. A turn no follow holds, or whose heartbeat is a minute old, its pod retired or gone, is
taken over by whichever pod notices first, on a `GET /api/inbox` or on the sweep each pod runs every 15 seconds,
as a new task (`turn.task` changes) that goes on from the file: a message the session has not
taken is sent, once, and the stream is read from the last offset, past the messages on file,
which the reply keeps. Only the follow the file names writes the reply, so it lands once, with
every message of the turn in it once. Through a rollout's overlap a page on the pod not following
the turn gets no progress, and its next read brings the reply.

**Shutdown.** A pod lets go of its follows as its shutdown is announced (the chart's preStop
drain), before the shutdown waits on anything: its sweep stops, and a follow holding the turn is
released and written out of the file, the turn kept in flight with no follow holding it, so the
other pod takes it over at its next read or sweep without waiting out the minute. A follow waiting
for an environment it started walks away from that wait, and one that begins after the
announcement leaves at once. A pod shutting down takes no turn over, and quietly: its
`GET /api/inbox` answers the sitting as it stands.

**Failures.** A controller answer that it does not know the environment or the session (`404
not-found`), or that the environment is not running (`409 conflict`), loses the sitting: `lost` is
set, the turn cleared with no reply, and the transcript kept. A Start also loses it when the
project has no running or stopped environment, or the one it started did not settle. Any other
failure, a `5xx`, an unreachable controller or a dropped stream, is tried again after 2 seconds,
the wait doubling up to 60, for as long as the follow holds the turn.

**End** clears the file first, then ends the session and stops the environment, but only one the
API started and only while no other session is working in it, and neither for a sitting the expiry
has closed already. A lost sitting's session is gone, and the worker may since have given its name
to another session, an actioner run's, so End ends none for it and counts every session working in
the environment. What the controller no longer knows, or no longer runs, counts as closed; any
other failure there is logged and left, so End always answers `204`.

**The idle expiry.** The same sweep closes a sitting with no turn in flight once it is due, as End
closes it, and keeps it. A live sitting is due once its `moved` is `FIELDNOTES_KUBECODER_INBOX_IDLE`
minutes old (30), and is kept with status `ended`, its `ended` set. A lost one is due once that time
has passed since `lost.at`, not since its last send, and stays lost; it reads the same on the wire
before and after. Either is marked closed in the write that keeps it, so it is closed once: no
later sweep, and neither Start nor End, which clear it, ends its session or stops its environment
again. Both pods sweep, under the file's lock.

The log names each Start with its prompt and each turn by its length, not its text.

## Errors

Every non-2xx response is RFC 9457 `application/problem+json`: `type` (a slug from a closed set),
`title`, `status`, `detail` (what to do next), and for validation errors an `errors` list of
`{loc, msg, type}` that never echoes the input.

| Slug | Status | When |
| --- | --- | --- |
| `unauthenticated` | 401 | no bearer, or one that resolves to no client; a webhook delivery whose signature or token does not verify |
| `not-found` | 404 | no observation by that id; it may have been merged into another, so the detail suggests posting again; no triage item by that id in `triage/` |
| `conflict` | 409 | the observation's file no longer parses (the detail names the fault); a board sync of an observation with no card; a card YouTrack does not have; a ruling or take-back on a triage item that is submitted, rewritten since the page loaded, or not valid; an inbox Start or turn with the inbox not configured; a turn without a live sitting |
| `session-busy` | 409 | an inbox turn while a turn, a Start's included, is in flight: refused, never queued |
| `validation-error` | 422 | a request that does not validate, a malformed id; a `no` or `later` ruling without a note |
| `not-ready` | 503 | the store is still being cloned or the index built |
| `models-unreachable` | 502 | the models pod did not answer the embedding a match needs: a post without `force`, match and neighbors |
| `store-unreachable` | 502 | the store's remote could not be reached, a write's fetch included, or refused the push; nothing was written |
| `board-unreachable` | 502, 503 | 502: YouTrack could not be reached or refused the read; 503: no board is configured |
| `internal` | 500 | anything unmapped, in fixed words, with the traceback in the log |

A path no route matches gets the template's own 404 body, outside this model.

## Startup

Cloning the store and building the index run on a thread of their own, since a cold cache can take
minutes. The health checks answer meanwhile. `/health/readyz` (its `store` entry) and every
endpoint that needs the index answer 503 (`not-ready`) until startup is done. A startup that fails
ends the process in production, so the pod is restarted: the template's liveness check always
answers 200. The GitHub webhook queues its pull even before startup is done.

Outside production a backend without `FIELDNOTES_STORE_URL` runs without a store, so the dev stack
boots: its Fieldnotes endpoints answer `not-ready`. The frontend's Playwright backend, in testing
mode, always serves an empty store of its own, a bare repo in a temporary directory indexed with
the fake models, so the triage queue it opens on answers empty, and serves its inbox configured and
its actioner startable over a fake KubeCoder controller; any `FIELDNOTES_*` in its environment,
such as the dev instance's `.env`, is ignored. A Playwright test lays out what it needs through two
surfaces that answer only in testing mode, outside it `400`, and are listed in the OpenAPI document
untyped, as the template's testing endpoints are:

- `/api/testing/store`: `PUT {"files": {path: text}}` makes `observations/` and `triage/` those
  files, in one commit through the write queue, indexed before the `204`; `GET` answers them as the
  store holds them, for a test to read what the UI wrote.
- `/api/testing/controller`: `PUT {inbox, environments, turns, run_turns, runs}` replaces the fake
  controller with one holding those environments of the store's project, each with the names of
  the headless sessions `working` in it (a running one only), the turns the session's next sends
  play (their log lines and messages, in the order the log holds them, their result, a pace between
  them, or held until `finish`), the turn each
  accepted actioner run's session plays, in order, and the answers to the actioner's prompt runs,
  the inbox configured or not; the sitting the old one held is ended as End ends it, and the
  actioner starts afresh, with no hold and no failure. The backend starts with no environment, so a
  test that opens the inbox, or wants a run's lines, lays one out. An accepted run plays in a new
  headless session of the first running environment, else of the first stopped one, which it
  starts; a run past `run_turns` plays no line and is over at once. The layout has the run's
  follow look every quarter of a second. A turn's line is a string, a condensed log line, or a
  message, `{text, final}`, `final` false unless given: the fake writes a message into the session's
  log as the worker does, and the stream frames it as a `message` of the turn in flight with the
  offset after its line. The turn's result is the layout's own, so a test whose reply should not
  repeat it lays it out as the final messages' texts joined with blank lines. `POST …/emit {line}`
  and `POST …/finish {result, is_error}` play a line and the result of the sitting's turn in
  flight, and `POST …/run-emit` and
  `POST …/run-finish`, with the same bodies, those of the last run the fake accepted, whose session
  `run-finish` ends; each waits up to 5 seconds for its turn to be in flight. `POST …/lose {what:
  session|environment}` loses the sitting at the controller, `POST …/expire` closes the sitting as
  the idle expiry does once its time has passed, now, and `POST …/run-end {outcome, reason}`
  delivers the webhook of the last run the fake accepted; `GET` answers the environments, their
  sessions and the messages sent to each, and the runs accepted. A body that does not validate is
  `422 validation-error`, nothing to play `409 conflict`, such as no turn in flight within the
  wait, or for `expire` a sitting with a turn in flight, one closed already, or none.

## The pinned surface

The contract models in `backend/packages/fieldnotes-contracts` are the agents' surface's definition:
the endpoints validate with them directly, and `backend/tests/fieldnotes/test_surface.py` pins every
route, model field, enum value and problem slug by equality, so a change to the surface is a visible
diff. See [change-discipline.md](change-discipline.md) for the rule this enforces. The agents' routes
are in the template's OpenAPI document as well, since Spectree in its default mode lists every route
no other Spectree instance decorated, but untyped: they are not decorated, so the document carries
neither their bodies nor a role.

The triage and inbox endpoints are not part of the pinned surface. They are the UI's, typed in the
OpenAPI document with the app's own models (`backend/app/fieldnotes/triage.py`, `actioner.py` and
`inbox.py`) and marked with their `editor` gate (`x-required-role`); the UI generates its client
from that document. Their problem slugs are the closed set's, `session-busy` among them, which only
an inbox turn answers.

## Settings

Read once at startup from environment variables; a malformed value fails startup and names its
variable; secrets only ever come from the environment.

| Variable | Default | What |
| --- | --- | --- |
| `FIELDNOTES_STORE_URL` | required | the store repo's remote |
| `FIELDNOTES_STORE_TOKEN` | none | a GitHub token for the remote (secret), sent as an HTTP header through git's environment |
| `FIELDNOTES_STORE_DIR` | `/data/store` | the checkout |
| `FIELDNOTES_STORE_BRANCH` | `main` | |
| `FIELDNOTES_GIT_AUTHOR_NAME`, `FIELDNOTES_GIT_AUTHOR_EMAIL` | `Fieldnotes API`, `fieldnotes-api@noreply.localhost` | the author of the API's commits |
| `FIELDNOTES_CACHE_DIR` | `/data/cache` | the embedding cache, the actioner's hold and the inbox sitting |
| `FIELDNOTES_MODELS_URL` | `http://models.models-prd.svc.cluster.local` | the models pod |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | the embedding model's name, the cache's key |
| `FIELDNOTES_MATCH_*` | | see [match-pipeline.md](match-pipeline.md) |
| `FIELDNOTES_CLIENT_TOKEN_<NAME>` | | a named client's bearer (secret) |
| `FIELDNOTES_GITHUB_WEBHOOK_SECRET`, `FIELDNOTES_GITHUB_REPO` | none | see [webhooks.md](webhooks.md); set together or not at all |
| `FIELDNOTES_YOUTRACK_URL`, `FIELDNOTES_YOUTRACK_TOKEN` | none | see [webhooks.md](webhooks.md); set together or not at all |
| `FIELDNOTES_YOUTRACK_RESOLUTION_FIELD`, `FIELDNOTES_YOUTRACK_OUTCOMES` | see webhooks.md | |
| `FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN`, `FIELDNOTES_YOUTRACK_WEBHOOK_HEADER` | none, `X-YouTrack-Token` | see [webhooks.md](webhooks.md) |
| `FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE` | 5 | seconds between a YouTrack delivery and the read of its card; see [webhooks.md](webhooks.md) |
| `FIELDNOTES_KUBECODER_URL`, `FIELDNOTES_KUBECODER_TOKEN`, `FIELDNOTES_KUBECODER_REPO` | none | the KubeCoder controller, the API's client token there (secret) and the store's KubeCoder project (`owner/Name`); set together or not at all; the actioner and the inbox sitting both need them |
| `FIELDNOTES_KUBECODER_WEBHOOK_URL` | none | the address the controller reaches `/api/hooks/kubecoder` at; it needs the three above, and without it a submit starts no actioner; see [The actioner start](#the-actioner-start) |
| `FIELDNOTES_KUBECODER_ACTIONER_PROMPT`, `FIELDNOTES_KUBECODER_ACTIONER_MODEL`, `FIELDNOTES_KUBECODER_ACTIONER_EFFORT` | the store's prompt, the engine's defaults | the actioner's prompt, model and reasoning effort |
| `FIELDNOTES_KUBECODER_INBOX_PROMPT` | `/dev:inbox` | the inbox sitting's first message, the operator's scope appended; see [The inbox sitting](#the-inbox-sitting) |
| `FIELDNOTES_KUBECODER_INBOX_CWD` | `Fieldnotes/inbox` | the sitting's session's working directory, `/work`-relative or absolute |
| `FIELDNOTES_KUBECODER_INBOX_MODEL`, `FIELDNOTES_KUBECODER_INBOX_EFFORT` | the engine's defaults | the sitting's model and reasoning effort |
| `FIELDNOTES_KUBECODER_INBOX_IDLE` | 30 | minutes a sitting with no turn in flight goes unmoved, or a lost one stays lost, before the API closes it and keeps it; see [The inbox sitting](#the-inbox-sitting); a positive number |
| `FIELDNOTES_KUBECODER_INBOX_TRACKER` | `https://issues.webathome.org/issue/` | the issue address the inbox screen appends a card id to |
| `FIELDNOTES_API_HOST`, `FIELDNOTES_API_PORT`, `FIELDNOTES_LOG_LEVEL` | | read but unused since the port: the template's `HOST` and `PORT` and its logging apply |
