# Slice testing strategy

How a slice is proven once its phases are merged. This is the doc `.aiworkflowrc` names as
`test_phase.strategy`: the run loop's test phase is "read this and execute it", and nothing else
names it. Read it top to bottom and do what it says.

## What this phase proves, and what it does not

**Verification here is local.** Nothing is deployed yet: there is no dev instance and no production
one, and a push to `main` builds and deploys nothing. So this phase runs the suites in this
environment, from the merged working tree, and then pushes.

There is no `devlock`: with no dev instance, nothing contends.

The slice that first deploys a service changes all three of those sentences, and rewrites this doc in
the same slice.

## 0. Preconditions

The driver has ff-merged every code phase into the base branch. Confirm the tree is clean
(`git status --short`) before starting. A dirty tree here means an earlier phase left something
behind, and that is a finding, not something to tidy away.

Every verb below runs from the repo root, because `kc project` is cwd-bound.

## 1. The suites

```bash
kc project build
kc project test
kc project lint
```

All three must be green. No gate is known red, so a failure is this slice's. A `kc project` verb
prints its output only on failure, so read its exit code directly and never through a pipe.

While the manifest declares no `build` verb, `kc project build` is a skip and proves nothing; `test`
and `lint` carry the gate. Say so in the close-out report rather than reporting the build green.

## 2. The live check

`fieldnotes-api` runs in the `python` tool container against a store generated under the gitignored
`.run/` and the real models pod. The store lives in `.run/` and not in `/tmp` because every
container has its own `/tmp`, while the checkout is shared; the tool containers share the pod's
network, so the API answers on `localhost` here. All texts are invented.

Boot, from the repo root:

```bash
rm -rf .run/live && mkdir -p .run/live
git init --quiet --bare --initial-branch main .run/live/remote.git
cat > .run/live/env <<'EOF'
FIELDNOTES_STORE_URL=/work/FieldnotesApp/.run/live/remote.git
FIELDNOTES_STORE_DIR=/work/FieldnotesApp/.run/live/checkout
FIELDNOTES_CACHE_DIR=/work/FieldnotesApp/.run/live/cache
FIELDNOTES_CLIENT_TOKEN_MCP=live-mcp-token
FIELDNOTES_CLIENT_TOKEN_SKILLS=live-skills-token
FIELDNOTES_GITHUB_WEBHOOK_SECRET=live-github-secret
FIELDNOTES_GITHUB_REPO=pvginkel/Fieldnotes
FIELDNOTES_API_PORT=8765
EOF
cexec python sh -c 'cd /work/FieldnotesApp && set -a && . .run/live/env && set +a &&
  setsid nohup .venv/bin/fieldnotes-api > .run/live/api.log 2>&1 < /dev/null &
  echo $! > .run/live/api.pid'
until curl -sf localhost:8765/readyz; do sleep 1; done
```

The pid is written from inside the tool container: killing the local `cexec` client does not reach
the process it started, and `.venv/bin/fieldnotes-api` rather than `uv run` keeps that pid the
server's own.

Drive it with `curl`, `Authorization: Bearer live-mcp-token` (or `live-skills-token`):

1. `POST /observations` a novel observation: `201` with an id, and one commit on
   `.run/live/remote.git`'s `main`.
2. Post a paraphrase of it: `200`, the first as a `likely` candidate, and no new commit. Post an
   unrelated one: `201`.
3. `POST /observations/{id}/reactions` on the candidate, then `GET` it: the reaction is there with
   its provenance, and `last_seen` and `repos` moved.
4. `POST /match` and `GET /observations/{id}/neighbors` with the skills token.
5. The GitHub-webhook row: clone `.run/live/remote.git`, rewrite one observation's `canonical`, push,
   then post a delivery signed with `live-github-secret` (`X-GitHub-Event: push`, a body naming
   `pvginkel/Fieldnotes` and `refs/heads/main`, `X-Hub-Signature-256` from
   `openssl dgst -sha256 -hmac`). It answers `queued`; `GET` shows the new canonical and
   `.run/live/api.log` says `embedded 1 texts`. The same delivery with a wrong signature is a `401`.
6. The index-rebuild row: stop the API, delete `.run/live/cache`, boot it again. `/neighbors` answers
   byte for byte as before, and the log says one text embedded per observation.

Stop:

```bash
cexec python sh -c 'kill $(cat /work/FieldnotesApp/.run/live/api.pid)'
```

The board sync has no live check here: it needs YouTrack's webhook app pointed at a deployed API.
The same goes for `fieldnotes-mcp` when it arrives: the slice that makes it runnable writes its check
by running it.

The "Validation" table in [`design.md`](design.md) lists the checks that land here or in `eval/` as
the services come to exist: the model smoke (`eval/smoke.py`), the replay and eval of gate 1
(`eval/replay.py`, `eval/run.py`, over the private dataset by path), the index rebuild and the
GitHub webhook (above), the MCP end-to-end, the board sync. A slice that delivers what one of those
rows tests brings the row to life in the same slice, as a script rather than a described manual step
wherever it can be one. A slice that changes the match pipeline or its thresholds reruns the replay
and the eval, and keeps their output out of this repo: it quotes the dataset.

Until then, a slice whose acceptance criteria need a running MCP server or a real board reports
those criteria as *not verified*, never as passed.

## 3. Check off `verification.json`

Mark each acceptance criterion with the evidence that settled it: the command run and what it
returned, or the call made and what it answered. A criterion nothing in steps 1–2 exercised has not
passed by inspection. It is either an untested criterion (a finding) or a check this doc is missing.

## 4. Push

Pushing is this phase's job. The driver ff-merges locally and never pushes a code phase, then checks
before the doc phase that every repo in `state.json`'s `bases` reached `origin`. Push each one,
honouring any repo named in `plan.md`'s `## Push holds`.

No CI job follows a push yet, so there is no build to confirm.

## Findings

Blocking findings come back as appended phases. Findings below the bar go in the close-out report
for the operator to triage. A check that cannot run at all (no credential, a port already held, a
service unreachable) is reported as *not verified*, never as passed. The phase may end with a
criterion unproven if it says so. It may not end with one assumed.

## The operator gate

The operator's gate is the close-out report, after the run.
