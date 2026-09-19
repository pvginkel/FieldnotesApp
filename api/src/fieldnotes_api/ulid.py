"""ULIDs: an observation's id and file name (FR-8). 48 bits of milliseconds, 80 random bits, in
Crockford base32, so ids sort by creation time."""

from __future__ import annotations

import os
import re
from datetime import datetime

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
_PATTERN = re.compile(PATTERN)


def new_ulid(at: datetime) -> str:
    value = (int(at.timestamp() * 1000) << 80) | int.from_bytes(os.urandom(10))
    return "".join(_ALPHABET[(value >> (5 * shift)) & 31] for shift in reversed(range(26)))


def is_ulid(text: str) -> bool:
    return bool(_PATTERN.match(text))
