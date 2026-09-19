"""Webhook verification and payload reading (FR-20; design, "Webhooks").

The GitHub delivery reaches the API through the relay, raw bytes and signature headers intact, and
is verified again here over the exact bytes received: `X-Hub-Signature-256` is `sha256=` and the
hex HMAC-SHA256 of the body under the shared secret. The webhook must deliver
`application/json`.
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
