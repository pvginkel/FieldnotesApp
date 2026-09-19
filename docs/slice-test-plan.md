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

While the manifest declares no verbs, all three are skips and prove nothing; say so in the close-out
report rather than reporting green.

## 2. The live check

**There is none yet, and this section is not to be filled from imagination.** The slice that makes
`fieldnotes-api` runnable writes the check by running it: boot, drive, and the stop recipe included,
with the pid written from inside the tool container to a gitignored `.run/`, because killing the local
`cexec` client does not reach the process it started. The same goes for `fieldnotes-mcp` when it
arrives.

The "Validation" table in [`design.md`](design.md) lists the checks that will land here or in `eval/`
as the services come to exist: the model smoke, the index rebuild, the GitHub webhook (good and bad
signature), the MCP end-to-end, the board sync. A slice that delivers what one of those rows tests
brings the row to life in the same slice, as a script rather than a described manual step wherever it
can be one.

Until then, a slice whose acceptance criteria need a running service, real models or a real board
reports those criteria as *not verified*, never as passed.

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
