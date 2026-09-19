"""The API's settings, read once at startup from `FIELDNOTES_*` environment variables.

| Variable | Default | What |
| --- | --- | --- |
| `FIELDNOTES_STORE_URL` | required | the store repo's remote |
| `FIELDNOTES_STORE_TOKEN` | none | a GitHub token for the remote (secret) |
| `FIELDNOTES_STORE_DIR` | `/data/store` | the checkout, on the volume |
| `FIELDNOTES_STORE_BRANCH` | `main` | |
| `FIELDNOTES_GIT_AUTHOR_NAME`, `_EMAIL` | `Fieldnotes API` | the server's commits' author |
| `FIELDNOTES_CACHE_DIR` | `/data/cache` | the embedding cache, on the volume |
| `FIELDNOTES_MODELS_URL` | the models pod's Service | `/embed` and `/rerank` |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | what the pod embeds with: the cache's key |
| `FIELDNOTES_MATCH_COSINE_TOP` | 8 | candidates by cosine |
| `FIELDNOTES_MATCH_BM25_TOP` | 4 | candidates by BM25 |
| `FIELDNOTES_MATCH_LIKELY` | 0.8, provisional | the high threshold on the rerank score |
| `FIELDNOTES_MATCH_RELATED` | 0.5, provisional | the low threshold |
| `FIELDNOTES_MATCH_GAP` | 0.3, provisional | how far a candidate may trail the best |
| `FIELDNOTES_MATCH_BOTH_DIRECTIONS` | `false` | rerank each pair both ways and average |
| `FIELDNOTES_CLIENT_TOKEN_<NAME>` | | the bearer of the named client `<name>` (secret) |
| `FIELDNOTES_API_HOST`, `_PORT` | `0.0.0.0`, 8080 | where the API listens |
| `FIELDNOTES_LOG_LEVEL` | `INFO` | |

Tokens are never in code or config files, only in environment variables materialised from
secrets. A malformed value fails startup and names its variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .auth import ClientRegistry
from .matching import MatchSettings
from .store import StoreSettings

PREFIX = "FIELDNOTES_"
CLIENT_TOKEN_PREFIX = f"{PREFIX}CLIENT_TOKEN_"

DEFAULT_MODELS_URL = "http://models.models-prd.svc.cluster.local"
DEFAULT_EMBED_MODEL = "BAAI/bge-base-en-v1.5"

# Provisional until gate 1: plan step 4 reads the thresholds and the gap from the eval run's score
# distributions and commits them here.
DEFAULT_LIKELY = 0.8
DEFAULT_RELATED = 0.5
DEFAULT_GAP = 0.3


class SettingsError(RuntimeError):
    """A variable is missing or malformed. The message names it."""


@dataclass(frozen=True)
class Settings:
    store: StoreSettings
    cache_dir: Path
    models_url: str
    embed_model: str
    match: MatchSettings
    clients: ClientRegistry
    host: str
    port: int
    log_level: str


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    """The variable's value, or None when it is unset or blank."""
    return environ.get(PREFIX + name, "").strip() or None


def _get(environ: Mapping[str, str], name: str, default: str) -> str:
    return _optional(environ, name) or default


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _optional(environ, name)
    if value is None:
        raise SettingsError(f"{PREFIX}{name} is not set")
    return value


def _number[T: (int, float)](environ: Mapping[str, str], name: str, kind: type[T], default: T) -> T:
    value = _optional(environ, name)
    if value is None:
        return default
    try:
        return kind(value)
    except ValueError:
        raise SettingsError(f"{PREFIX}{name} is not a number: {value!r}") from None


def _flag(environ: Mapping[str, str], name: str) -> bool:
    value = _get(environ, name, "false").lower()
    if value not in ("true", "false"):
        raise SettingsError(f"{PREFIX}{name} is neither true nor false: {value!r}")
    return value == "true"


def _match(environ: Mapping[str, str]) -> MatchSettings:
    match = MatchSettings(
        cosine_top=_number(environ, "MATCH_COSINE_TOP", int, 8),
        bm25_top=_number(environ, "MATCH_BM25_TOP", int, 4),
        likely=_number(environ, "MATCH_LIKELY", float, DEFAULT_LIKELY),
        related=_number(environ, "MATCH_RELATED", float, DEFAULT_RELATED),
        gap=_number(environ, "MATCH_GAP", float, DEFAULT_GAP),
        both_directions=_flag(environ, "MATCH_BOTH_DIRECTIONS"),
    )
    if not 0 <= match.related <= match.likely <= 1:
        raise SettingsError(
            f"{PREFIX}MATCH_RELATED ({match.related}) and {PREFIX}MATCH_LIKELY ({match.likely}) "
            "must satisfy 0 <= related <= likely <= 1"
        )
    if match.cosine_top < 1 or match.bm25_top < 0 or match.gap < 0:
        raise SettingsError(
            f"{PREFIX}MATCH_COSINE_TOP must be at least 1, and {PREFIX}MATCH_BM25_TOP and "
            f"{PREFIX}MATCH_GAP at least 0"
        )
    return match


def _clients(environ: Mapping[str, str]) -> ClientRegistry:
    return ClientRegistry(
        {
            name[len(CLIENT_TOKEN_PREFIX) :].lower(): token.strip()
            for name, token in environ.items()
            if name.startswith(CLIENT_TOKEN_PREFIX) and token.strip()
        }
    )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    environ = os.environ if environ is None else environ
    return Settings(
        store=StoreSettings(
            url=_required(environ, "STORE_URL"),
            root=Path(_get(environ, "STORE_DIR", "/data/store")),
            branch=_get(environ, "STORE_BRANCH", "main"),
            token=_optional(environ, "STORE_TOKEN"),
            author_name=_get(environ, "GIT_AUTHOR_NAME", "Fieldnotes API"),
            author_email=_get(environ, "GIT_AUTHOR_EMAIL", "fieldnotes-api@noreply.localhost"),
        ),
        cache_dir=Path(_get(environ, "CACHE_DIR", "/data/cache")),
        models_url=_get(environ, "MODELS_URL", DEFAULT_MODELS_URL),
        embed_model=_get(environ, "EMBED_MODEL", DEFAULT_EMBED_MODEL),
        match=_match(environ),
        clients=_clients(environ),
        host=_get(environ, "API_HOST", "0.0.0.0"),
        port=_number(environ, "API_PORT", int, 8080),
        log_level=_get(environ, "LOG_LEVEL", "INFO").upper(),
    )
