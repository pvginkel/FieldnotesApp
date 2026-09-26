FROM python:3.13-slim AS build

# uv uses the image's own Python and never downloads one; copy mode, because
# the cache and the venv are on different filesystems in a build.
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_LINK_MODE=copy
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# system deps only if you need to compile wheels; many projects don't
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
      build-essential \
      libffi-dev libssl-dev pkg-config \
   && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app

# --- Reproduce the environment ---
COPY pyproject.toml uv.lock /app/
# The workspace members are dependencies of the project, built from source.
COPY packages /app/packages
COPY mcp-server /app/mcp-server

# Install project deps into .venv, the members copied in (not linked), so the
# venv carries the `fieldnotes-mcp` entry point the pod's mcp container runs.
RUN uv sync --locked --no-dev --no-install-project --no-editable

# copy your source (no re-resolution needed)
COPY *.py /app/
COPY app /app/app

# Now let's build the runtime image from the builder.
#   We'll just copy the env and the PATH reference.
FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends tini netcat-openbsd \
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
