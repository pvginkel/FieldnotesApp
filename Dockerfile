# One image, two entry points: `fieldnotes-api` and `fieldnotes-mcp` come out of the one workspace
# venv, and the `fieldnotes` chart runs this image twice in the one pod, once per container. Built
# the way KubeCoder's images are — the build context is the workspace ROOT, so the members and
# `packages/fieldnotes-contracts` are all visible to `uv sync`, and it is single-stage on the
# upstream python image so the venv's python symlink stays valid at run time.
#
# 3.13 is the version the toolchain container and CI run, so the image runs what the suites ran.
FROM python:3.13-slim

# uv, pinned, copied from its own distroless image the way upstream documents. The pin is the
# version that wrote `uv.lock`; the base carries no uv, and `uv sync` below is the whole install.
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

# tini for correct PID-1 signal handling. git because the API owns the store as a real checkout and
# shells out to `git` for every fetch, rebase, commit and push (`fieldnotes_api.store`) — it is a
# runtime dependency, not a build one. ca-certificates so the HTTPS remote, the models pod and
# YouTrack all verify.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Both services into the one venv, named rather than `--all-packages`: `eval` is a workspace member
# too and stays out, being the replay and eval harness rather than a shipped service. --no-dev drops
# pytest and ruff; --no-editable copies fieldnotes-contracts in rather than linking it to
# /app/packages. The whole workspace still has to be in the context, because `uv sync` reads every
# member's pyproject.toml to resolve the lock.
ENV UV_PYTHON_PREFERENCE=only-system \
    UV_COMPILE_BYTECODE=1
RUN uv sync --frozen --no-dev --no-editable \
        --package fieldnotes-api \
        --package fieldnotes-mcp

ENV PATH="/app/.venv/bin:${PATH}"
# The API listens on 8080 and the MCP server on 8081 (`FIELDNOTES_API_PORT`, `FIELDNOTES_MCP_PORT`).
EXPOSE 8080 8081
ENTRYPOINT ["tini", "--"]
# The API is the default; the pod's `mcp` container overrides the command with `fieldnotes-mcp`.
CMD ["fieldnotes-api"]
