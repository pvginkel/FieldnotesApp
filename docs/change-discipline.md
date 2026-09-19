# Change discipline

The rules every code change in this repo obeys. This is the doc `.aiworkflowrc` names as
`design_philosophy`: it is handed to every `code-writer`, `code-reviewer`, `plan-writer` and
`plan-reviewer` the pipeline dispatches, and it is what a reviewer cites when sending work back.

The design is not here. It is in [`design.md`](design.md), and as the code that builds it lands, the
design as built goes into topic docs beside this one. Its "Decisions" table and its numbered
requirements are binding. A decision it leaves open is the operator's to rule, never a plan's or an
implementation's to assume.

## The server stays dumb

The value of Fieldnotes sits in the reconciler and in the post-time duplicate response; capture is
plumbing. All judgment lives in versioned skills that live with the store, not in this repo. The API
commits, indexes, matches and verifies webhooks. It holds no scheduler, no triage logic and no opinion
about which observations matter. A change that moves judgment into a service is the wrong change,
however convenient.

`fieldnotes-mcp` is thinner still: three tools mapped 1:1 onto the API, and nothing the API does not
already do.

## The "not doing" list is ruled

The design closes off agent-facing search, transcript mining, a triage UI, a vector database, a
TTL, a fine-tuned reranker, reconciler-authored documentation changes and a `bug` category, and names
the trigger that would reopen each. A slice does not build one of these, or the groundwork for one,
because it looked cheap while passing. When a trigger seems to have fired, say so in the close-out
report; reopening is the operator's ruling.

No search tool is the one with a reason worth restating: observations are unverified, and an agent
reading them as facts propagates errors. Nothing in this repo presents an observation to an agent as
established truth.

## The two surfaces are contracts

The MCP tools (`post`, `react`, `get`: names, parameters, result shapes, and the literal next step a
candidate carries) are what every agent's instructions are written against, and a running session
keeps the tool definitions it loaded when it started. The REST endpoints are what the reconciler and
actioner skills call from another repo. So a change to either surface is deliberate and reviewed:

- A test pins each surface, so a change to it is a visible diff in the slice that makes it, never a
  side effect.
- A slice that changes a surface says so in its close-out report, because the agent instructions and
  the skills that depend on it live in other repos and have to follow.

Behind the surfaces, internal interfaces change cleanly: the services deploy together and have no
other consumers. Change the code and fix the callers, with no shim, adapter or compatibility
parameter.

## The store is git, and git is the record

One markdown file per observation; every server write is a commit on `main`; the embedding cache is a
content-addressed directory on the API's volume that the server alone writes. There is no database,
and no state that matters lives only in the API's memory or on its volume: the index is rebuilt from
the repository, and deleting the cache must cost a reindex and nothing else. The file format (FR-8) is a storage layout that
skills and people edit by hand, so it changes as deliberately as a surface does.

Merged-away files are removed and history is the record. The same holds for this repo.

## No tombstones

Delete replaced code completely: no "moved to X" comments, forwarding stubs, deprecated aliases,
commented-out blocks or helpers kept in case they come back. The same holds for prose: when a
convention changes, rewrite the doc rather than appending a note that the old rule no longer holds.

## Validate at the boundary, nowhere else

No `try`/`except` that swallows an error, no silent fallback, no retry or cache without an observed
failure to point at, and no guard for a condition the types already rule out.

The boundaries are where checking is the feature: tool and endpoint input (the `category` enum, a
known observation id), and above all the webhooks, whose signature or token is verified on every
request before any processing (FR-20, NFR-4). An input validated at the boundary is not checked again
further in.

## Testability is critical

Every change ships with a test. A feature without one is incomplete, and "verified by hand" is no
substitute: the point of a test is that it runs again next time.

`kc project test` is hermetic. It reaches no model pod, no GitHub and no YouTrack: embeddings
come from a fake, the store is a repository the test creates, and webhooks are requests
the test signs itself. It is green from any environment with no credentials. Checks that need the
real models or a real board belong to the test phase ([`slice-test-plan.md`](slice-test-plan.md)) and
to the design's validation checks, not to the suite. Match *quality* is likewise not a unit test: it
is the eval run over labeled pairs, and thresholds are config set from that run.
