"""Webhook verification and payload reading (FR-20; design, "Webhooks").

The GitHub delivery reaches the API through the relay, raw bytes and signature headers intact, and
is verified again here over the exact bytes received: `X-Hub-Signature-256` is `sha256=` and the
hex HMAC-SHA256 of the body under the shared secret. The webhook must deliver
`application/json`.

The YouTrack delivery comes from JetBrains' Webhook Triggers app, in-cluster: a JSON body whose
`id` is the issue's readable id, and the shared token in a header the app names (its default is
`X-YouTrack-Token`). Of the payload only `id` is read; the card is then read from YouTrack itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GithubSettings:
    secret: str
    repo: str  # the store repo, `owner/name`


@dataclass(frozen=True)
class YouTrackHookSettings:
    token: str
    header: str


def youtrack_verified(settings: YouTrackHookSettings, presented: str | None) -> bool:
    return presented is not None and hmac.compare_digest(
        settings.token.encode(), presented.strip().encode()
    )


def youtrack_issue(body: bytes) -> str | None:
    """The readable id of the issue a YouTrack delivery names, or None."""
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return None
    issue = payload.get("id") if isinstance(payload, dict) else None
    return issue.strip() if isinstance(issue, str) and issue.strip() else None


def github_signature(secret: str, body: bytes) -> str:
    """The `X-Hub-Signature-256` value GitHub sends for this body."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def github_verified(secret: str, body: bytes, signature: str | None) -> bool:
    return signature is not None and hmac.compare_digest(
        github_signature(secret, body), signature.strip()
    )


def github_push_to(settings: GithubSettings, branch: str, event: str | None, body: bytes) -> bool:
    """Whether a verified delivery is a push to the store's branch."""
    if event != "push":
        return False
    try:
        payload: Any = json.loads(body)
        repo = payload["repository"]["full_name"]
        ref = payload["ref"]
    except (ValueError, KeyError, TypeError):
        return False
    return str(repo).lower() == settings.repo.lower() and ref == f"refs/heads/{branch}"
