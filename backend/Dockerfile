FROM python:3.13-slim AS build

ENV POETRY_HOME=/opt/poetry
ENV POETRY_VIRTUALENVS_IN_PROJECT=1
ENV POETRY_VIRTUALENVS_CREATE=1
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

RUN pip install poetry

WORKDIR /app

# --- Reproduce the environment ---
COPY pyproject.toml poetry.lock /app/

# Install project deps into .venv
RUN poetry install --only main --no-root --no-interaction --no-ansi

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
