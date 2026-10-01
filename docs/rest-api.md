# The REST API

This covers the HTTP surface the backend exposes: authentication, every endpoint and request
model, what each does, error shapes, startup behaviour and settings.

## Listening and authentication

The surface is the ModernAppTemplate backend's (`backend/`, Flask under waitress): it listens on
`HOST`:`PORT` (default `0.0.0.0:3401`), the Fieldnotes endpoints live under `/api`
(`backend/app/api/fieldnotes.py`), and the domain behind them is `backend/app/fieldnotes/`. The
UI's nginx in the same pod proxies `/api/` to it.

Every endpoint except the health checks, `GET /metrics`, the three webhooks and the triage endpoints
requires `Authorization: Bearer <token>` (NFR-4). These are `@public` to the template's OIDC hook,
which gates only the UI's own endpoints on the operator's session: the agents' surface checks its
client tokens itself. A token resolves to a named client configured as
`FIELDNOTES_CLIENT_TOKEN_<NAME>`, the name being the suffix lowercased (`mcp`, `skills`).
Resolution compares digests in constant time against every configured client. With no client
configured, every bearer is refused. The resolved client's name goes into the commit message of any
write, onto the reactions it writes (`client`), and into the log. The webhooks verify their own
secrets, but for KubeCoder's, which carries none and is acted on only for the run the actioner
holds; see [webhooks.md](webhooks.md).

The triage endpoints (`/api/triage/…`, `backend/app/api/triage.py`) are the triage UI's, and take
the operator's OIDC session instead (NFR-4): the template's, gated on the `editor` client role for
every method, a GET included. A request with no session, or with an agent's bearer token, is
refused with 401, and a signed-in user without the role with 403, both in the template's own
`{error}` body rather than problem+json.

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
  the item's file holds it. Returned items (a `question`, and not submitted since) come first, then
  the rest, each group by `written`, oldest first. `observations` maps each item's observation id to
  its fields as the observation index holds them, which is as they stand now unless the index lags
  (see [match-pipeline.md](match-pipeline.md#the-index)): the ten fields of an item's `snapshot`,
  spelled as the snapshot spells them, so an observation unchanged since its item was written equals
  the snapshot. It is null for an observation the index does not hold (merged away or deleted, or
  not taken in yet). The items come from the
  triage index, which reads `triage/*.json` at startup and after every pull and write. An item file
  that is not JSON, breaks the store's item rules (the app's own copy of the store's check) or does
  not parse is logged and left out.
- **Ruling** (FR-25): sets the item's `ruling` to `{verb, note, at: now, submitted: null}` over any
  draft. A returned item keeps its `question`; a ruling whose `at` is later than the question's is
  its answer. A `no` or a `later` without a note is refused (`validation-error`).
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
  now`) and answers with their ids. A returned item counts as ruled only once its ruling's `at` is
  later than its question's. With nothing ruled it writes nothing, starts nothing and answers `[]`.
  A submit that submitted something then starts the actioner, off the request: see
  [The actioner start](#the-actioner-start).
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
`react <id> <emoji> (<client>)`, `board-sync <id> <card>`, and the operator's `rule <id> <verb>
(operator)`, `unrule <id> (operator)`, `submit <n> (operator)`. A write fetches and resets to the
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
controller that cannot be reached is logged at ERROR and not retried. Each call, and each attempt
that found a live hold, is counted in `fieldnotes_actioner_starts_total`.

The run's outcome comes back to `POST /api/hooks/kubecoder` (see
[webhooks.md](webhooks.md#kubecoder-post-apihookskubecoder)), which releases the hold:

- **a success** starts the actioner again, which calls nothing unless items still wait, such as an
  item submitted while the run worked;
- **a skip for want of an environment**, `in-use` (every environment of the project busy) or
  `no-capacity` (none could be started), is tried again 300 seconds later, while items wait;
- **any other ending**, a failure or a `no-environment` skip, is logged at ERROR with its reason and
  the tail of the session's answer, and not retried: the next submit, or a session in the store's
  environment asked to action the triage, is the retry.

Each is counted in `fieldnotes_actioner_runs_total`.

At most one start is pending in a pod, from the submit that asked for it until the run starts, a
call fails or nothing waits; a submit through that pod meanwhile starts nothing, since the pending
start covers it. The pending start lives in the process alone: shutdown cancels a retry that waits,
a retired pod's included, and a pod that starts resumes none, so the items wait for the next submit
or the held run's webhook. The hold outlives the pod, and either pod may receive the webhook. Without
the four settings a submit still writes, and the start is skipped with a warning in the log.

## Errors

Every non-2xx response is RFC 9457 `application/problem+json`: `type` (a slug from a closed set),
`title`, `status`, `detail` (what to do next), and for validation errors an `errors` list of
`{loc, msg, type}` that never echoes the input.

| Slug | Status | When |
| --- | --- | --- |
| `unauthenticated` | 401 | no bearer, or one that resolves to no client; a webhook delivery whose signature or token does not verify |
| `not-found` | 404 | no observation by that id; it may have been merged into another, so the detail suggests posting again; no triage item by that id in `triage/` |
| `conflict` | 409 | the observation's file no longer parses (the detail names the fault); a board sync of an observation with no card; a card YouTrack does not have; a ruling or take-back on a triage item that is submitted, rewritten since the page loaded, or not valid |
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
the fake models, so the triage queue it opens on answers empty; any `FIELDNOTES_*` in its
environment, such as the dev instance's `.env`, is ignored. A Playwright test lays that store out
for itself through `/api/testing/store`, which answers only in testing mode and is not in the
OpenAPI document: `PUT {"files": {path: text}}` makes `observations/` and `triage/` those files, in
one commit through the write queue, indexed before the `204`; `GET` answers them as the store holds
them, for a test to read what the UI wrote.

## The pinned surface

The contract models in `backend/packages/fieldnotes-contracts` are the agents' surface's definition:
the endpoints validate with them directly, and `backend/tests/fieldnotes/test_surface.py` pins every
route, model field, enum value and problem slug by equality, so a change to the surface is a visible
diff. See [change-discipline.md](change-discipline.md) for the rule this enforces. The agents' routes
are in the template's OpenAPI document as well, since Spectree in its default mode lists every route
no other Spectree instance decorated, but untyped: they are not decorated, so the document carries
neither their bodies nor a role.

The triage endpoints are not part of the pinned surface. They are the UI's, typed in the OpenAPI
document with the app's own models (`backend/app/fieldnotes/triage.py`) and marked with their
`editor` gate (`x-required-role`); the UI generates its client from that document.

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
| `FIELDNOTES_CACHE_DIR` | `/data/cache` | the embedding cache |
| `FIELDNOTES_MODELS_URL` | `http://models.models-prd.svc.cluster.local` | the models pod |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | the embedding model's name, the cache's key |
| `FIELDNOTES_MATCH_*` | | see [match-pipeline.md](match-pipeline.md) |
| `FIELDNOTES_CLIENT_TOKEN_<NAME>` | | a named client's bearer (secret) |
| `FIELDNOTES_GITHUB_WEBHOOK_SECRET`, `FIELDNOTES_GITHUB_REPO` | none | see [webhooks.md](webhooks.md); set together or not at all |
| `FIELDNOTES_YOUTRACK_URL`, `FIELDNOTES_YOUTRACK_TOKEN` | none | see [webhooks.md](webhooks.md); set together or not at all |
| `FIELDNOTES_YOUTRACK_RESOLUTION_FIELD`, `FIELDNOTES_YOUTRACK_OUTCOMES` | see webhooks.md | |
| `FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN`, `FIELDNOTES_YOUTRACK_WEBHOOK_HEADER` | none, `X-YouTrack-Token` | see [webhooks.md](webhooks.md) |
| `FIELDNOTES_YOUTRACK_WEBHOOK_SETTLE` | 5 | seconds between a YouTrack delivery and the read of its card; see [webhooks.md](webhooks.md) |
| `FIELDNOTES_KUBECODER_URL`, `FIELDNOTES_KUBECODER_TOKEN`, `FIELDNOTES_KUBECODER_REPO`, `FIELDNOTES_KUBECODER_WEBHOOK_URL` | none | the KubeCoder controller, the API's client token there (secret), the store's KubeCoder project (`owner/Name`) and the address the controller reaches `/api/hooks/kubecoder` at; set together or not at all; see [The actioner start](#the-actioner-start) |
| `FIELDNOTES_KUBECODER_ACTIONER_PROMPT`, `FIELDNOTES_KUBECODER_ACTIONER_MODEL`, `FIELDNOTES_KUBECODER_ACTIONER_EFFORT` | the store's prompt, the engine's defaults | the actioner's prompt, model and reasoning effort |
| `FIELDNOTES_API_HOST`, `FIELDNOTES_API_PORT`, `FIELDNOTES_LOG_LEVEL` | | read but unused since the port: the template's `HOST` and `PORT` and its logging apply |
