# FieldnotesApp

The application behind **Fieldnotes**, a curated, cross-project observation store for AI agents: the
operator's complaint box. The friction agents run into, which they now leave in close-out reports
among thirty other findings, they post as an *observation* instead. `post` answers with what the
store already knows, the operator's ruling included, and the agent reacts to that instead of
re-posting: that post-time answer is the knowledge delivery. There is no search tool, because the
store is temporary: what is reported gets fixed or documented and then leaves it. A scheduled
reconciler session curates the store into a triage document, the operator rules on it, and an
actioner session, which the operator starts by hand, carries the rulings out.

**It is being built as a proof of concept**, to find out whether the idea proves its value. The design
is [`docs/design.md`](docs/design.md): the ruled decisions, the numbered requirements (`FR-n`,
`NFR-n`), the technical design and the validation checks. Read it before writing code, and cite its
requirement numbers in tests and commits. A decision it leaves open is the operator's to rule, not
something to settle in code.

The build follows
[`../FieldnotesAppSpecs/plan/implementation-plan.md`](../FieldnotesAppSpecs/plan/implementation-plan.md):
take the first unchecked item, check it off when it is done, and stop at a gate, which is the
operator's decision.

## Repo structure

One uv workspace, laid out like KubeCoder's, which `kc project` runs as one component, `root`
(`setup`, `lint`, `test`). One member's tests alone: `cexec python uv run --all-packages pytest api`.

- **`api/`**: `fieldnotes-api`, the Python REST service and the only component with logic — the git
  checkout and its commits, the file model, the in-memory index and its embedding cache, the match
  pipeline, the GitHub and YouTrack webhooks.
- **`mcp-server/`**: `fieldnotes-mcp`, the thin MCP server, built like KubeCoder's. Exactly three
  tools, `post`, `react` and `get`, mapped 1:1 onto the API.
- **`packages/fieldnotes-contracts/`**: the pydantic wire models both share.
- **`eval/`**: the replay and eval harness. Code and invented fixtures only.

Both services ship in one image and run as containers of one pod, next to a `webhook-relay`
container that is the only internet-facing part.

Other repos the work touches:

- **`../FieldnotesAppSpecs`**: the spec repo (the plan, the mined dataset, gate reports, and later
  slices). It is a separate git repo and a working tree shared by parallel sessions: commit there
  separately, and stage files by name, never `git add -A`.
- **The store**, `pvginkel/Fieldnotes`, private: `observations/`, `triage/`, and the `install`,
  `reconciler` and `actioner` skills. It is data plus skills, not application code, and nothing of it
  lives here.
- **`pvginkel/HelmCharts`** holds both charts: `models` (the Text Embeddings Inference pod, which no
  application owns) and `fieldnotes`. **`pvginkel/DockerImages`** holds `webhook-relay`.

The environment is composed by AIWorkflow's `.kubecoder/config.yaml`, which checks this repo out as a
sibling. Python runs in the `python` tool container, so an ad-hoc command is `cexec python uv …`. The
`kc project` verbs are cwd-bound: run them from this repo's root.

## Working rules

**Commit straight to `main`, small and often**, in this repo and the spec repo alike.

**This repo is public; the spec repo is private.** No secret or token, and no excerpt of a real
observation, goes into a commit here. Test fixtures are invented; the mined dataset stays in the spec
repo and reaches the eval harness by path.

## The dev pipeline

The greenfield build does not use slices: it follows the implementation plan. Once the plan is done,
code changes go through the `dev` plugin's slice workflow (`/dev:triage` → `/dev:plan-slice` →
`/dev:run-slice`), which the operator drives. The project's half of that contract is `.aiworkflowrc`
and `.kubecoder/project.yaml`; nothing the pipeline reads by machine belongs in this file.

Issue tracking follows the host convention. This project's owner tag is **`FieldnotesApp`**, and its
YouTrack project key is **`FN`**.

## Key documentation

- [`docs/design.md`](docs/design.md): the design, its decisions and its numbered requirements.
- The services as built: [`docs/rest-api.md`](docs/rest-api.md),
  [`docs/observation-file.md`](docs/observation-file.md),
  [`docs/match-pipeline.md`](docs/match-pipeline.md), [`docs/webhooks.md`](docs/webhooks.md),
  [`docs/mcp-server.md`](docs/mcp-server.md).
- [`docs/change-discipline.md`](docs/change-discipline.md): the rules every change obeys.
- [`docs/slice-test-plan.md`](docs/slice-test-plan.md): how a slice is verified before it is pushed.
- [`docs/slice-doc-plan.md`](docs/slice-doc-plan.md): which doc surfaces a shipped slice updates.
