# FieldnotesApp

The application behind **Fieldnotes**, a curated, cross-project observation store for AI agents. It
takes the place of agent memory: what agents now leave in close-out reports, where it is ignored, they
post as an *observation* instead. `post` answers with likely duplicates and their reaction counts, and
the agent reacts to one instead of re-posting — that post-time answer is the knowledge delivery, and
the reason there is no search tool. A scheduled reconciler session curates the store into a triage
document, the operator rules on it, and an actioner session turns rulings into YouTrack issues.

**Nothing is built yet.** The design is
[`../FieldnotesAppSpecs/design/design-note.md`](../FieldnotesAppSpecs/design/design-note.md): the ruled
decisions, the numbered requirements (`FR-n`, `NFR-n`), the technical design, the seven-step plan and
the test runbook. Read it before writing code, and cite its requirement numbers in slices and tests. A
decision it leaves open is the operator's to rule, not something to settle in code.

## Repo structure

This repo holds the three runtime deliverables the design names. Where each lands on disk is the
first slice's to decide; `kc project list` shows the components as they come to exist.

- **`fieldnotes-api`**: the Python REST service, and the only component with logic — the git checkout
  and its commits, the file model, the in-memory index, the match pipeline, the GitHub and YouTrack
  webhooks, the embedding cache branch.
- **`fieldnotes-mcp`**: the thin MCP server, built like the existing KubeCoder MCP servers. Exactly
  three tools, `post`, `react` and `get`, mapped 1:1 onto the API.
- **`fieldnotes-models`**: one pod, NGINX in front of two Text Embeddings Inference containers
  (`bge-base-en-v1.5`, `bge-reranker-base`).

Two repos sit beside it:

- **`../FieldnotesAppSpecs`**: the spec repo (the design note, slices, plans and each run's state). It
  is a separate git repo and a working tree shared by parallel sessions: commit there separately, and
  stage files by name, never `git add -A`.
- **The store** is a third repo that does not exist yet: `observations/`, `triage/`, the `install`,
  `reconciler` and `actioner` skills, and `eval/`. It is data plus skills, not application code, and
  nothing of it lives here. Creating and naming it is the operator's call.

The environment is composed by AIWorkflow's `.kubecoder/config.yaml`, which checks this repo out as a
sibling. Python runs in the `python` tool container, so an ad-hoc command is `cexec python uv …`. The
`kc project` verbs are cwd-bound: run them from this repo's root.

## Working rules

**Commit straight to `main`, small and often**, in this repo and the spec repo alike.

**This repo is public; the spec repo is private.** No secret or token, and no excerpt of a real
observation, goes into a commit here. Test fixtures are invented.

## The dev pipeline

Code changes go through the `dev` plugin's slice workflow (`/dev:triage` → `/dev:plan-slice` →
`/dev:run-slice`), which the operator drives. The project's half of the contract is `.aiworkflowrc`
and `.kubecoder/project.yaml`; nothing the pipeline reads by machine belongs in this file.

Issue tracking follows the host convention. This project's owner tag is **`FieldnotesApp`**, and its
YouTrack project key is **`FN`**.

## Key documentation

- [`docs/change-discipline.md`](docs/change-discipline.md): the rules every change obeys.
- [`docs/slice-test-plan.md`](docs/slice-test-plan.md): how a slice is verified before it is pushed.
- [`docs/slice-doc-plan.md`](docs/slice-doc-plan.md): which doc surfaces a shipped slice updates.
