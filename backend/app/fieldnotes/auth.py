"""Bearer auth with named clients (NFR-4; design, "Services").

A caller presents a static bearer token that resolves to a named client, `mcp` or `skills`,
configured as `FIELDNOTES_CLIENT_TOKEN_<NAME>`. The name is logged with each write and recorded on
the reactions a client writes. With no client configured every bearer is refused.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class ClientRegistry:
    """The configured clients, each held as its name and its token's digest. Resolving compares
    the presented token's digest against every record in constant time, with no early exit, so
    the time taken says nothing about which client matched or how nearly."""

    def __init__(self, tokens: Mapping[str, str]) -> None:
        self._records = tuple((name, _digest(token)) for name, token in sorted(tokens.items()))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._records)

    def resolve(self, token: str) -> str | None:
        presented = _digest(token)
        matched = None
        for name, stored in self._records:
            if secrets.compare_digest(presented, stored):
                matched = name
        return matched


def bearer(authorization: str | None) -> str | None:
    """The token of an `Authorization: Bearer <token>` header, or None."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None
