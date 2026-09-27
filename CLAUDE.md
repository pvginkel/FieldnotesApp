# FieldnotesApp

The application behind **Fieldnotes**, a curated, cross-project observation store for AI agents: the
operator's complaint box. The friction agents run into, which they now leave in close-out reports
among thirty other findings, they post as an *observation* instead. `post` answers with what the
store already knows, the operator's ruling included, and the agent reacts to that instead of
re-posting: that post-time answer is the knowledge delivery. There is no search tool, because the
store is temporary: what is reported gets fixed or documented and then leaves it. A scheduled
reconciler session curates the store into triage items, the operator rules on them in the app's
triage UI, and an actioner session, which Submit starts through a KubeCoder timer, carries the
rulings out.

**It is a proof of concept, built and live since 2026-09-21**, to find out whether the idea proves
its value: `fieldnotes-prd` is the one deployment, every agent on the host is told to post to it,
and a timer runs the reconciler each morning. **A push to `main` builds and rolls production.** The
design is [`docs/design.md`](docs/design.md): the ruled decisions, the numbered requirements
(`FR-n`, `NFR-n`), the technical design and the validation checks. Read it before writing code,
and cite its requirement numbers in tests and commits. A decision it leaves open is the operator's
to rule, not something to settle in code.

The greenfield build followed
[`../FieldnotesAppSpecs/plan/implementation-plan.md`](../FieldnotesAppSpecs/plan/implementation-plan.md),
which is now the record of how it was built and why: its notes hold what the deployment taught
(the seven OpenBao leaves, why `enableServiceLinks` is off, the webhook's settle time), and its last
two items, which wait on real use, are the only ones open.

## Repo structure

A ModernAppTemplate monorepo (root, `backend/`, `frontend/`, each with its `.copier-answers.yml`),
on uv rather than the template's Poetry: the operator's ruling, carried in the template-owned files
too (see "Template ownership" below). `kc project` runs `root`, `backend` and `frontend`.

- **Root**: the `run-suite` orchestrator (`tools/suite_runner/`, CI's single entry point), the
  shared `Procfile.dev` dev stack and its `scripts/dev.py` launcher, and the `Jenkinsfile`.
- **`backend/`**: the Flask backend, a uv workspace, and the only component with logic. The
  Fieldnotes domain is `app/fieldnotes/` (the git checkout and its write queue, the file model, the
  in-memory index and its embedding cache, the match pipeline, the GitHub and YouTrack webhooks,
  the triage index, the operator's rulings and the actioner start), its surfaces under `/api`
  `app/api/fieldnotes.py`, the agents', and `app/api/triage.py`, the triage UI's, and its wiring
  `app/services/fieldnotes_service.py`.
  The workspace members: **`packages/fieldnotes-contracts/`**, the pydantic wire models the API and
  the MCP server share; **`mcp-server/`**, `fieldnotes-mcp`, the thin MCP server with exactly three
  tools, `post`, `react` and `get`, mapped 1:1 onto the API, shipped as the image's second entry
  point; **`eval/`**, the replay and eval harness, code and invented fixtures only, never in the
  image. All their suites run in the backend's pytest: `cexec modern-app sh -c 'cd backend && uv
  run pytest'`.
- **`frontend/`**: the React + Vite SPA (TypeScript, pnpm) with TanStack Router/Query, an
  OpenAPI-generated client, and a Playwright E2E suite that boots the backend per worker.

The backend, the UI's nginx, the SSE gateway and the MCP server run as containers of one pod, next
to a `webhook-relay` container that is the only internet-facing part.

Other repos the work touches:

- **`../FieldnotesAppSpecs`**: the spec repo (the plan, the mined dataset, gate reports, and later
  slices). It is a separate git repo and a working tree shared by parallel sessions: commit there
  separately, and stage files by name, never `git add -A`.
- **The store**, `pvginkel/Fieldnotes`, private: `observations/`, `triage/`, and the `install`,
  `reconciler` and `actioner` skills. It is data plus skills, not application code, and nothing of it
  lives here.
- **`pvginkel/FieldnotesDeploy`** holds the `fieldnotes` chart and its prd values, which CI pins the
  image into; Argo CD syncs it. **`pvginkel/ModelsDeploy`** holds `models` (the Text Embeddings
  Inference pod, which no application owns). Both left `pvginkel/HelmCharts` on 2026-09-26.
  **`pvginkel/DockerImages`** holds `webhook-relay`.

The environment is composed by AIWorkflow's `.kubecoder/config.yaml`, which checks this repo out as a
sibling. The toolchain is the `modern-app` sidecar (Node, pnpm, uv), so an ad-hoc command is
`cexec modern-app …`. The `kc project` verbs are cwd-bound: run them from this repo's root.

## Template ownership

The root, `backend/` and `frontend/` are each generated from a ModernAppTemplate Copier template
(`.copier-answers.yml` in each). Files are either **template-owned** (overwritten by
`copier update`) or **app-owned** (generated once, never overwritten). Never modify a
template-owned file to add app behaviour; use the extension points instead:

| Need | Put it in |
|------|-----------|
| New API endpoints | `backend/app/api/`, registered in `register_blueprints()` in `backend/app/startup.py` |
| New services | `backend/app/services/`, wired in `backend/app/services/container.py` |
| CLI commands, post-migration logic | `backend/app/startup.py` |
| Test fixtures | `backend/tests/conftest.py`, `frontend/tests/support/fixtures.ts` |
| Sidecars CI must wait for | `backend/scripts/wait-for-services.py` (the suite runner calls it) |

If the template lacks an extension point you need, add it to the template first.

The one exception, the operator's ruling: the app is on uv, not Poetry, and the uv migration edits
template-owned files (`Jenkinsfile`, `tools/suite_runner/local.py`, `Procfile.dev`,
`scripts/dev.py`, `backend/scripts/dev-server.sh` and `testing-server.sh`). A `copier update`
three-way merges them; resolve a conflict there to the uv side. The template takes uv on later.

## Working rules

**Commit straight to `main`, small and often**, in this repo and the spec repo alike.

**This repo is public; the spec repo is private.** No secret or token, and no excerpt of a real
observation, goes into a commit here. Test fixtures are invented; the mined dataset stays in the spec
repo and reaches the eval harness by path.

## The dev pipeline

Code changes go through the `dev` plugin's slice workflow (`/dev:triage` → `/dev:plan-slice` →
`/dev:run-slice`), which the operator drives. The project's half of that contract is `.aiworkflowrc`
and `.kubecoder/project.yaml`; nothing the pipeline reads by machine belongs in this file. The
store's skills (`pvginkel/Fieldnotes`) are prose the operator and a session change together, outside
the slice workflow.

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
