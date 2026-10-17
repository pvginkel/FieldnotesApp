# The build image (DockerImages, modern-app-backend-build) carries Python 3.13,
# uv, the -dev packages wheels compile against, and uv's settings: the image's
# own Python, never a downloaded one, and copy mode.
FROM registry:5000/modern-app-backend-build AS build

WORKDIR /app

# --- Reproduce the environment ---
COPY pyproject.toml uv.lock /app/
# The workspace members are dependencies of the project, built from source.
COPY packages /app/packages
COPY mcp-server /app/mcp-server
# The eval harness is a member too, in the dev group only: --locked still reads its pyproject.
COPY eval/pyproject.toml /app/eval/pyproject.toml

# Install project deps into .venv, the members copied in (not linked), so the
# venv carries the `fieldnotes-mcp` entry point the pod's mcp container runs.
RUN uv sync --locked --no-dev --no-install-project --no-editable

# copy your source (no re-resolution needed)
COPY *.py /app/
COPY app /app/app

# The runtime image (DockerImages, modern-app-backend) carries tini, netcat and
# the shared libraries: libmagic, the image codecs and libpq.
FROM registry:5000/modern-app-backend

# git because the store is a real checkout the API fetches, commits to and pushes through the
# git CLI; ca-certificates so the HTTPS remote, the models pod and YouTrack all verify.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates \
   && rm -rf /var/lib/apt/lists/*

EXPOSE 3401

WORKDIR /app

COPY --from=build /app/.venv /app/.venv
COPY --from=build /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    DATA_PATH=/app/data \
    FLASK_ENV=production

ENTRYPOINT ["tini", "--"]

CMD ["python","run.py"]
