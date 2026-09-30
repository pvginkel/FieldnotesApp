# Slice testing strategy

How a slice is proven once its phases are merged. This is the doc `.aiworkflowrc` names as
`test_phase.strategy`: the run loop's test phase is "read this and execute it", and nothing else
names it. Read it top to bottom and do what it says.

## What this phase proves, and what it does not

**There is one deployment, and it is production.** A push to `main` runs the `FieldnotesApp` Jenkins
job (setup, lint, test, the images), which pins the images in `pvginkel/FieldnotesDeploy`, from
which Argo CD rolls `fieldnotes-prd`: the pod every agent on the host posts to, over the store the
reconciler curates every morning.
There is no dev instance. So this phase proves the slice **locally first** (the suites, then the
services run here against a generated store and the real models), pushes only what that proved,
confirms the rollout, and then checks the deployment for what only it can show.

Two things follow from production being the only instance:

- **What a check writes there is real.** A test observation is matched against by every agent's
  `post`, and can reach the operator word for word on a triage item. Run a deployed
  check only when the slice changes what it tests, use the invented `fieldnotes-e2e/*` data, and
  clear it in the same phase (step 5).
- There is no `devlock`: nothing is occupied for the length of a check. Two slices that push close
  together both deploy, the later image holds both, and a deployed check run against it still
  proves the earlier slice.

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

## 2. The local live check

The backend runs in the `modern-app` tool container, as the production image runs it
(`FLASK_ENV=production`, no reloader), against a store generated under the gitignored `backend/.run/`
and the real models pod. The store lives in `.run/` and not in `/tmp` because the checkout is what
every container shares for certain; the tool containers share the pod's network, so the API answers
on `localhost` here. All texts are invented.

Boot, from `backend/` (after `kc project setup`, which builds `backend/.venv`):

```bash
rm -rf .run/live && mkdir -p .run/live
git init --quiet --bare --initial-branch main .run/live/remote.git
cat > .run/live/env <<'EOF'
FLASK_ENV=production
SECRET_KEY=live-secret-key-live-secret-key-00
OIDC_ENABLED=false
FIELDNOTES_STORE_URL=/work/FieldnotesApp/backend/.run/live/remote.git
FIELDNOTES_STORE_DIR=/work/FieldnotesApp/backend/.run/live/checkout
FIELDNOTES_CACHE_DIR=/work/FieldnotesApp/backend/.run/live/cache
FIELDNOTES_CLIENT_TOKEN_MCP=live-mcp-token
FIELDNOTES_CLIENT_TOKEN_SKILLS=live-skills-token
FIELDNOTES_GITHUB_WEBHOOK_SECRET=live-github-secret
FIELDNOTES_GITHUB_REPO=pvginkel/Fieldnotes
EOF
cexec modern-app sh -c 'cd /work/FieldnotesApp/backend && set -a && . .run/live/env && set +a &&
  setsid nohup .venv/bin/dev > .run/live/api.log 2>&1 < /dev/null &
  echo $! > .run/live/api.pid'
until curl -s localhost:3401/health/readyz | grep -q '"store":{"failed":false,"ok":true}'; do sleep 1; done
```

The API answers on 3401 under `/api`. `/health/readyz` as a whole stays `503`, because it also
waits for the SSE gateway, which this check does not run; its `store` entry is the API's own
readiness. The pid is written from inside the tool container: killing the local `cexec` client does
not reach the process it started, and `.venv/bin/dev` rather than `uv run dev` keeps that pid the
server's own.

Drive it with `curl` at `localhost:3401/api`, `Authorization: Bearer live-mcp-token` (or
`live-skills-token`):

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
7. The triage row. `OIDC_ENABLED=false` makes the template's authentication hook skip every
   request, so `/api/triage/…` answers `curl` with no session; the `editor` gate is the suite's to
   prove. In a clone of `.run/live/remote.git`, add two invented items as `triage/<id>.json` for
   observations steps 1 and 2 created, shaped as the suite's `item()` in
   `tests/fieldnotes/test_triage.py` builds them: a plain one, and a returned one (a ruling and a
   `question`, no `submitted`). Push, and let the API pull with a delivery as in step 5.
   `GET /triage/queue` lists the returned item first, each observation beside it. Rule both
   (`PUT …/ruling` with the `written` the queue showed), take one back (`DELETE
   …/ruling?written=…`), rule it again, then `POST /triage/submit`: each write is one commit on the
   remote before its reply (`rule <id> <verb> (operator)`, `unrule <id> (operator)`,
   `submit 2 (operator)`), a second submit answers `[]` and commits nothing, and the log says the
   actioner is not started, since this env sets no `FIELDNOTES_KUBECODER_*`. Last, the store's own
   rules over what the API wrote:
   `python3 /work/Fieldnotes/skills/reconciler/reconcile.py --store .run/live/checkout check`
   finds every file valid.

`fieldnotes-mcp` runs beside it, as the API's `mcp` client, the same way:

```bash
cat > .run/live/mcp.env <<'EOF'
FIELDNOTES_API_TOKEN=live-mcp-token
FIELDNOTES_MCP_TOKEN=live-agent-token
EOF
cexec modern-app sh -c 'cd /work/FieldnotesApp/backend && set -a && . .run/live/mcp.env && set +a &&
  setsid nohup .venv/bin/fieldnotes-mcp > .run/live/mcp.log 2>&1 < /dev/null &
  echo $! > .run/live/mcp.pid'
until curl -sf localhost:8081/readyz; do sleep 1; done
```

Its defaults are the pod's: the API at `http://localhost:3401/api`, listening on 8081.

8. The MCP row, which is a script:

   ```bash
   cexec modern-app sh -c 'cd /work/FieldnotesApp/backend && .venv/bin/python eval/mcp_e2e.py --token live-agent-token'
   ```

   It drives the server with a real MCP client over the Streamable-HTTP transport, the way an
   agent does, and checks: a `POST /mcp` with no bearer, or a wrong one, is a `401`; the tools are
   exactly `get`, `post` and `react`; a novel post creates an observation; a paraphrase of it from
   another repo creates nothing and comes back as a `likely` candidate with its reaction counts
   and its next step; a reaction on it is appended with its repo, session and client `mcp`, and
   moves `repos` and `last_seen`; the same paraphrase with `force` creates a second observation;
   and an unknown id and the `bug` category are tool errors that say why. It exits non-zero naming
   the check that failed, and prints the ids it wrote — three commits on `.run/live/remote.git`,
   which `git -C .run/live/remote.git log --oneline main` shows are the two posts and the
   reaction, and nothing for the paraphrase.

   Each run takes the first of its eight invented scenarios the store does not hold already, so
   the script can be run against a store it has run against before until the pool runs out.
   `--url` points it at a deployed server instead of the local one, and then it writes its
   observations into the real store: that is step 4, with its conditions.

Stop:

```bash
cexec modern-app sh -c 'kill $(cat /work/FieldnotesApp/backend/.run/live/mcp.pid) $(cat /work/FieldnotesApp/backend/.run/live/api.pid)'
```

The board sync has no local check: it needs YouTrack's webhook app, which points at the
deployment. Its live check is in step 4.

The "Validation" table in [`design.md`](design.md) lists the checks that land here or in `eval/` as
the services come to exist: the model smoke (`eval/smoke.py`), the replay and eval of gate 1
(`eval/replay.py`, `eval/run.py`, over the private dataset by path), the index rebuild and the
GitHub webhook (above), the MCP end-to-end (`eval/mcp_e2e.py`), the board sync. A slice that delivers what one of those
rows tests brings the row to life in the same slice, as a script rather than a described manual step
wherever it can be one. A slice that changes the match pipeline or its thresholds reruns the replay
and the eval, and keeps their output out of this repo: it quotes the dataset. A slice that changes
the scorer itself (the embedding model, the lexical weight, a new ingredient) first compares it with
the present one in `eval/bench.py`, at matched false-alarm rates, since a threshold does not carry
from one scorer to the next; `eval/embed_local.py` embeds the dataset with a model the pod does not
serve.

## 3. Push, and confirm the rollout

Pushing is this phase's job. The driver ff-merges locally and never pushes a code phase, then checks
before the doc phase that every repo in `state.json`'s `bases` reached `origin`. Push each one,
honouring any repo named in `plan.md`'s `## Push holds`.

A push of this repo builds and deploys. The build writes its image pins into
`pvginkel/FieldnotesDeploy` as a commit on that repo's `main`, and Argo CD syncs the `fieldnotes-prd`
Application from there. Follow the build through to the roll:

```bash
timeout 1200 track_build.py FieldnotesApp --hash "$(git rev-parse HEAD)" --appear-timeout 120 --diagnose
```

It needs `JENKINS_TOKEN`, which this environment projects, and the environment's FieldnotesDeploy
clone (`../FieldnotesDeploy`), which it fetches to follow the pin commit. Once the build is green it
reads the pin line from the build's console and waits until `fieldnotes-prd` has rolled that
commit: the build takes a few minutes, the roll about one more, and the roll deadline is ten minutes,
hence the `timeout`. Its exit status says where it stopped:

| Exit | Meaning | What to do |
|------|---------|------------|
| `0` | The build is green and `fieldnotes-prd` rolled the pin commit. | Go on to the pod checks below. |
| `1` | The build failed; nothing was followed into Argo CD. | `--diagnose` printed the console tail; the full log's path is in the summary. A finding. |
| `3` | An operational problem: Jenkins or Kubernetes auth, a build that never appeared, a failed fetch. | Fix the cause and run it again; the build is not re-run. |
| `4` | The build is green, but the environment has no clone of the deploy repo it pushed to. | The summary gives the `git clone` line. This environment declares FieldnotesDeploy, so this is an environment fault, not a slice finding. |
| `5` | The roll failed: the sync failed, an error condition, or the Application settled `Degraded` or `Missing`. | A finding; the diagnosis written beside the logs names the failing resources. |
| `6` | The Application had not rolled the commit ten minutes after Argo CD saw it. | A finding; the diagnosis is on disk as for `5`. |
| `7` | Argo CD will not roll the commit on its own: it never saw the pin commit (a lost webhook), or the sync is not automated. | A finding. Argo CD does not poll this Application (its reconciliation timeout is off), so waiting longer does not help. |

The second job, `AaC/FieldnotesApp` (`Jenkinsfile.architecture`), validates and archives
`docs/architecture/*.yaml`; follow it the same way when the slice touched the artifact (it deploys
nothing, so it ends at the build).

**A rolled, `Healthy` Application is not proof of a good deploy on its own.** Look at the pod,
read-only, through the `iac` tool container:

```bash
cexec iac kubectl -n fieldnotes-prd get pods
cexec iac kubectl -n fieldnotes-prd get pod -o jsonpath='{.items[0].status.containerStatuses[0].imageID}'
curl -sI -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
  http://registry:5000/v2/fieldnotes/manifests/<build> | grep -i docker-content-digest
curl -s -o /dev/null -w '%{http_code}\n' https://fieldnotes-api.home/health/readyz
curl -s -o /dev/null -w '%{http_code}\n' https://fieldnotes-mcp.home/readyz
curl -s -o /dev/null -w '%{http_code}\n' https://fieldnotes/
```

The pod is `5/5 Running`, its image digest is the build's, and all three answer `200`. The
deployment is `Recreate`, so the service is away for about a minute during the roll: a `502` then
is the roll, not a finding. The API's log is
`cexec iac kubectl -n fieldnotes-prd logs deploy/fieldnotes -c app`.

A slice that changes the chart pushes `pvginkel/FieldnotesDeploy` as well. The build's pin commits
land on that repo's `main` too, so fetch and rebase the chart commit onto `origin/main` before
pushing it; a rejected push means a pin commit landed first, so rebase again, never force. The chart push rolls the pod
on its own, and no build follows it, so confirm the roll by hand: the Application's revision is the
chart commit (or a later pin commit), `Synced`, `Healthy`:

```bash
git -C ../FieldnotesDeploy fetch -q && git -C ../FieldnotesDeploy log -1 --format='%H %s' origin/main
cexec iac kubectl -n argocd-prd get application fieldnotes-prd \
  -o jsonpath='{.status.sync.revision} {.status.sync.status} {.status.health.status}{"\n"}'
```

Then the pod checks above. Push the chart first when the new image needs what the chart adds, the image first
when the chart needs the new image, and either when neither does. A slice that changes only the
store's skills (`pvginkel/Fieldnotes`) deploys nothing; the next reconciler run is what uses it.

## 4. The deployed checks

Only what the local run cannot show, and only when the slice touches it. The MCP end-to-end and
the board sync write to the production store or to the board, so each ends with step 5.

**The MCP end-to-end**, when the slice changes the MCP server, the API's `post`/`react`/`get` path,
the image or the chart:

```bash
cexec modern-app sh -c 'cd /work/FieldnotesApp/backend && set -a && . ../.env && set +a &&
  .venv/bin/python eval/mcp_e2e.py --url https://fieldnotes-mcp.home/mcp'
```

The bearer is `FIELDNOTES_MCP_TOKEN` in the gitignored `.env` of this checkout, put there by the
operator; no environment can read the deployment's secrets, so an environment without that file
reports this check as *not verified*. The endpoints are https with a step-ca certificate: `curl`
and the system Python trust it, a client that verifies against `certifi` does not (the script
passes the system context for that reason).

**Board sync and card feedback**, when the slice changes the YouTrack webhook, `board.py` or what a
sync writes. It takes the YouTrack MCP tools, a push to the store, and patience of five seconds a
step, the webhook's settle time ([webhooks.md](webhooks.md)):

1. Raise a trial issue in `FN` that says it is one. In `pvginkel/Fieldnotes`, set a
   `fieldnotes-e2e/*` test observation's `card` to it and its `status` to `raised`
   (`python3 skills/reconciler/reconcile.py set <id> card=FN-<n> status=raised`), commit, push.
2. Comment a workaround on the issue. Within ten seconds the store gets one `board-sync` commit
   whose `card_updated` is the comment's own time, to the second. A time one event old means the
   card was read before YouTrack committed the change.
3. A second sync writes nothing: `reconcile.py board-scan` answers `unchanged` and `origin/main`
   does not move. It needs the API's `skills` token, which only an environment of
   `pvginkel/Fieldnotes` holds, as does a `--dry-run` reconciler run, whose edit should carry the
   workaround. From here that is a headless session in that environment, through the fleet tools.
4. Move the issue to Done with resolution Resolved and a `Resolved: <pointer>` comment: one commit,
   `closed` / `done` with the pointer, from one read however many deliveries the change made. Change
   the resolution to Won't Do: `outcome: wont-do`.

Every sync is a commit on the store and a line in the API's log, so both are read from there.

**The triage endpoints**, when the slice changes them, the image or the chart. Reads only: a
ruling on prd is the operator's real ruling, so the writes are proved in step 2 and never here, and
no agent holds an `editor` session, so what the queue lists is the operator's to confirm.
`GET https://fieldnotes-api.home/api/triage/queue` with no session, and with an agent's bearer,
answers `401`. On `https://fieldnotes-api.home/metrics`, `fieldnotes_triage_queue` equals the
store's open items (the `triage/*.json` on `pvginkel/Fieldnotes`'s `origin/main` whose ruling is
absent or not submitted), and `fieldnotes_triage_rulings_total` and
`fieldnotes_actioner_starts_total` carry every label from zero.

**The dashboard**, when the slice changes `grafana/dashboard.py` or the metrics its panels read.
Upload it once the roll is confirmed:

```bash
cexec modern-app uv run grafana/dashboard.py --upload
```

It regenerates `grafana/fieldnotes.json` (commit it with the slice if it moved) and writes the
dashboard over the one in Grafana, by its uid `fieldnotes`; Grafana answers `'status': 'success'`
with the new version (the same version when nothing changed). It reads `GRAFANA_URL` and
`GRAFANA_TOKEN`, which this environment projects. Then prove each new or changed panel shows prd
series: run its expression through Grafana's Prometheus datasource, whose uid is the script's
`DATASOURCE`:

```bash
curl -s -H "Authorization: Bearer $GRAFANA_TOKEN" \
  "$GRAFANA_URL/api/datasources/proxy/uid/ce0kvu6exy9z4c/api/v1/query" \
  --data-urlencode 'query=<the panel expression>'
```

`"status":"success"` with a non-empty `result`; an empty one is a finding.

**The reconciler and the actioner** are not deployed services: they are skills in the store repo,
proven by a `--dry-run` session over a generated store and a local API
([the gate-2 handover](../../FieldnotesAppSpecs/handovers/gate-2-and-what-follows.md), section 4),
never over production.

## 5. Clear what the checks wrote

In `pvginkel/Fieldnotes`, `git rm` every observation whose `repos` are `fieldnotes-e2e/*`
(`grep -l fieldnotes-e2e observations/*.md`), commit and push; close the trial issue as Won't Do
with a comment. Test data left overnight is in the reconciler's morning queue, and can reach the
operator on a triage item. `observations/` keeps its `.gitkeep`: the
store's helpers refuse a root without that directory.

## 6. Check off `verification.json`

Mark each acceptance criterion with the evidence that settled it: the command run and what it
returned, or the call made and what it answered. A criterion nothing in steps 1–4 exercised has not
passed by inspection. It is either an untested criterion (a finding) or a check this doc is missing.

## Findings

Blocking findings come back as appended phases. Findings below the bar go in the close-out report
for the operator to triage. A check that cannot run at all (no credential, a port already held, a
service unreachable) is reported as *not verified*, never as passed. The phase may end with a
criterion unproven if it says so. It may not end with one assumed.

## The operator gate

The operator's gate is the close-out report, after the run.
