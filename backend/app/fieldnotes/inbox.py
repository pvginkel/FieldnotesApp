"""The inbox sitting (FR-28 to FR-32; design, "The inbox sitting"): the `dev` plugin's `/dev:inbox`
in a KubeCoder headless session in an environment of the store's project, which the API relays.

The sitting is configured by the controller's address, the API's token there and the project, the
actioner's own; the rest defaults here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

# The skill's own command; the operator's scope is appended to it.
DEFAULT_PROMPT = "/dev:inbox"
# `/work`-relative: the store's checkout in its environment. Its CLAUDE.md tells the session that
# the operator reads only a turn's last message.
DEFAULT_CWD = "Fieldnotes/inbox"
DEFAULT_IDLE = timedelta(minutes=30)
# The issue address a browser opens, a card id appended: the one the triage screen links cards
# to (`frontend/src/lib/triage/queue.ts`).
DEFAULT_TRACKER = "https://issues.webathome.org/issue/"


@dataclass(frozen=True)
class InboxSettings:
    prompt: str = DEFAULT_PROMPT
    cwd: str = DEFAULT_CWD
    # A sitting with no turn for this long is ended (FR-31).
    idle: timedelta = DEFAULT_IDLE
    model: str | None = None  # None: the engine's default
    reasoning_effort: str | None = None
    tracker: str = DEFAULT_TRACKER
