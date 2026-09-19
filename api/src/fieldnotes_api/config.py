"""The API's settings, read once at startup from `FIELDNOTES_*` environment variables.

| Variable | Default | What |
| --- | --- | --- |
| `FIELDNOTES_STORE_URL` | required | the store repo's remote |
| `FIELDNOTES_STORE_TOKEN` | none | a GitHub token for the remote (secret) |
| `FIELDNOTES_STORE_DIR` | `/data/store` | the checkout, on the volume |
| `FIELDNOTES_STORE_BRANCH` | `main` | |
| `FIELDNOTES_GIT_AUTHOR_NAME`, `_EMAIL` | `Fieldnotes API` | the server's commits' author |
| `FIELDNOTES_CACHE_DIR` | `/data/cache` | the embedding cache, on the volume |
| `FIELDNOTES_MODELS_URL` | the models pod's Service | `/embed` |
| `FIELDNOTES_EMBED_MODEL` | `BAAI/bge-base-en-v1.5` | what the pod embeds with: the cache's key |
| `FIELDNOTES_MATCH_LIKELY` | 0.94 | the high threshold on the match score |
| `FIELDNOTES_MATCH_RELATED` | 0.85 | the low threshold |
| `FIELDNOTES_MATCH_GAP` | 0.05 | how far a candidate may trail the best |
| `FIELDNOTES_MATCH_LEXICAL_WEIGHT` | 0.25 | the lexical overlap's weight in the score |
| `FIELDNOTES_CLIENT_TOKEN_<NAME>` | | the bearer of the named client `<name>` (secret) |
| `FIELDNOTES_GITHUB_WEBHOOK_SECRET` | none | the GitHub webhook's secret (secret) |
| `FIELDNOTES_GITHUB_REPO` | none | the store repo as GitHub names it, `owner/name` |
| `FIELDNOTES_YOUTRACK_URL` | none | YouTrack, for reading cards |
| `FIELDNOTES_YOUTRACK_TOKEN` | none | a read-only YouTrack token (secret) |
| `FIELDNOTES_YOUTRACK_RESOLUTION_FIELD` | `Resolution` | the field a card's outcome follows |
| `FIELDNOTES_YOUTRACK_OUTCOMES` | the design's map | `<value>=<outcome>`, comma-separated |
| `FIELDNOTES_YOUTRACK_WEBHOOK_TOKEN` | none | the YouTrack webhook's shared token (secret) |
| `FIELDNOTES_YOUTRACK_WEBHOOK_HEADER` | `X-YouTrack-Token` | the header it arrives in |
| `FIELDNOTES_API_HOST`, `_PORT` | `0.0.0.0`, 8080 | where the API listens |
| `FIELDNOTES_LOG_LEVEL` | `INFO` | |

Tokens are never in code or config files, only in environment variables materialised from
secrets. A malformed value fails startup and names its variable. The GitHub webhook's two
variables are set together or not at all, and so are YouTrack's URL and token; without the
webhooks' secrets every delivery is refused, and without YouTrack no card can be synced.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fieldnotes_contracts import Outcome

from .auth import ClientRegistry
from .board import BoardSettings
from .hooks import GithubSettings, YouTrackHookSettings
from .matching import MatchSettings
from .store import StoreSettings

PREFIX = "FIELDNOTES_"
CLIENT_TOKEN_PREFIX = f"{PREFIX}CLIENT_TOKEN_"

DEFAULT_MODELS_URL = "http://models.models-prd.svc.cluster.local"
DEFAULT_EMBED_MODEL = "BAAI/bge-base-en-v1.5"

# Read at gate 1 from the eval of the mined dataset (`eval/bench.py`, confirmed by a replay), for
# the cosine of `DEFAULT_EMBED_MODEL` plus `DEFAULT_LEXICAL_WEIGHT` times the lexical overlap, the
# scorer the operator ruled. A threshold belongs to its scorer: another embedding model or
# lexical weight needs its own. Related: the lowest score at which at most one novel post in
# ten is answered with a candidate. Likely: where three answers in four are right. The gap: the
# widest that still trims an answer; it costs no duplicate on the dataset.
DEFAULT_LIKELY = 0.94
DEFAULT_RELATED = 0.85
DEFAULT_GAP = 0.05
DEFAULT_LEXICAL_WEIGHT = 0.25

DEFAULT_RESOLUTION_FIELD = "Resolution"
# FR-21's map: Resolved and Absorbed are done, Won't Do is wont-do.
DEFAULT_OUTCOMES = "Resolved=done,Absorbed=done,Won't Do=wont-do"
DEFAULT_YOUTRACK_WEBHOOK_HEADER = "X-YouTrack-Token"


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
    github: GithubSettings | None
    board: BoardSettings | None
    youtrack_hook: YouTrackHookSettings | None
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


def _match(environ: Mapping[str, str]) -> MatchSettings:
    match = MatchSettings(
        likely=_number(environ, "MATCH_LIKELY", float, DEFAULT_LIKELY),
        related=_number(environ, "MATCH_RELATED", float, DEFAULT_RELATED),
        gap=_number(environ, "MATCH_GAP", float, DEFAULT_GAP),
        lexical_weight=_number(environ, "MATCH_LEXICAL_WEIGHT", float, DEFAULT_LEXICAL_WEIGHT),
    )
    if match.lexical_weight < 0 or match.gap < 0:
        raise SettingsError(
            f"{PREFIX}MATCH_LEXICAL_WEIGHT and {PREFIX}MATCH_GAP must be at least 0"
        )
    # A score is a cosine plus the weighted lexical overlap, itself about 0-1.
    highest = 1 + match.lexical_weight
    if not 0 <= match.related <= match.likely <= highest:
        raise SettingsError(
            f"{PREFIX}MATCH_RELATED ({match.related}) and {PREFIX}MATCH_LIKELY ({match.likely}) "
            f"must satisfy 0 <= related <= likely <= {highest:g}"
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


def _github(environ: Mapping[str, str]) -> GithubSettings | None:
    secret = _optional(environ, "GITHUB_WEBHOOK_SECRET")
    repo = _optional(environ, "GITHUB_REPO")
    if secret is None and repo is None:
        return None
    if secret is None or repo is None:
        raise SettingsError(
            f"{PREFIX}GITHUB_WEBHOOK_SECRET and {PREFIX}GITHUB_REPO are set together or not at all"
        )
    return GithubSettings(secret=secret, repo=repo)


def _outcomes(environ: Mapping[str, str]) -> dict[str, Outcome]:
    name = f"{PREFIX}YOUTRACK_OUTCOMES"
    outcomes: dict[str, Outcome] = {}
    for pair in _get(environ, "YOUTRACK_OUTCOMES", DEFAULT_OUTCOMES).split(","):
        value, _, outcome = pair.partition("=")
        try:
            outcomes[value.strip()] = Outcome(outcome.strip())
        except ValueError:
            raise SettingsError(
                f"{name}: {pair.strip()!r} is not `<value>=<outcome>` with an outcome of "
                f"{', '.join(o.value for o in Outcome)}"
            ) from None
    return outcomes


def _board(environ: Mapping[str, str]) -> BoardSettings | None:
    url = _optional(environ, "YOUTRACK_URL")
    token = _optional(environ, "YOUTRACK_TOKEN")
    if url is None and token is None:
        return None
    if url is None or token is None:
        raise SettingsError(
            f"{PREFIX}YOUTRACK_URL and {PREFIX}YOUTRACK_TOKEN are set together or not at all"
        )
    return BoardSettings(
        url=url,
        token=token,
        resolution_field=_get(environ, "YOUTRACK_RESOLUTION_FIELD", DEFAULT_RESOLUTION_FIELD),
        outcomes=_outcomes(environ),
    )


def _youtrack_hook(environ: Mapping[str, str]) -> YouTrackHookSettings | None:
    token = _optional(environ, "YOUTRACK_WEBHOOK_TOKEN")
    if token is None:
        return None
    header = _get(environ, "YOUTRACK_WEBHOOK_HEADER", DEFAULT_YOUTRACK_WEBHOOK_HEADER)
    return YouTrackHookSettings(token=token, header=header)


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
        github=_github(environ),
        board=_board(environ),
        youtrack_hook=_youtrack_hook(environ),
        host=_get(environ, "API_HOST", "0.0.0.0"),
        port=_number(environ, "API_PORT", int, 8080),
        log_level=_get(environ, "LOG_LEVEL", "INFO").upper(),
    )
