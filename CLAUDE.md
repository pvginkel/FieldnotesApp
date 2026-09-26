# FieldnotesApp

Curated, cross-project observation store for AI agents

## Repo structure

- **Root** — the `run-suite` orchestrator (`tools/suite_runner/`, CI's single entry point), the
  shared `Procfile.dev` dev stack and its `scripts/dev.py` launcher, and the `Jenkinsfile`.
- **`backend/`** — the Flask API (Python, uv).
- **`frontend/`** — the React + Vite SPA (TypeScript, pnpm) with TanStack Router/Query, an
  OpenAPI-generated client, and a Playwright E2E suite that boots the backend per worker.

Everything builds through `kc project build|test|lint|setup`, run **from the repo root** — those
verbs are cwd-bound. The toolchain lives in the `modern-app` sidecar, so ad-hoc `uv`/`pnpm`
commands need `cexec modern-app`. `scripts/dev.py` starts the dev stack. After a backend API change,
`scripts/regenerate-openapi.py --frontend` refreshes the frontend's OpenAPI cache and client.

## Template ownership

The root, `backend/` and `frontend/` are each generated from a ModernAppTemplate Copier template
(`.copier-answers.yml` in each). Files are either **template-owned** (overwritten by
`copier update`) or **app-owned** (generated once, never overwritten). Never modify a
template-owned file to add app behaviour; use the extension points instead:

| Need | Put it in |
|------|-----------|
| New API endpoints | `backend/app/api/`, registered in `register_blueprints()` in `backend/app/startup.py` |
| New services | `backend/app/services/`, wired in `backend/app/services/container.py` |
| Side effects after a request's commit | `after_commit()` from `backend/app/utils/after_commit.py` |
| CLI commands, post-migration logic | `backend/app/startup.py` |
| Test fixtures | `backend/tests/conftest.py`, `frontend/tests/support/fixtures.ts` |
| Sidecars CI must wait for | `backend/scripts/wait-for-services.py` (the suite runner calls it) |

If the template lacks an extension point you need, add it to the template first.
