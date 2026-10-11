"""The access log keeps the SSE gateway's callback secret out of its lines.

The gateway takes only a callback URL, so the secret rides in its query string
(`/api/sse/callback?secret=…`), and Waitress's access logger, paste's `TransLogger` on the
`wsgi` logger, writes every request URI as it came. A filter on that logger masks the value
before a line is written.
"""

from __future__ import annotations

import logging
import re

ACCESS_LOGGER = "wsgi"  # TransLogger's default logger name
_SECRET = re.compile(r"([?&]secret=)[^&\s\"]*")


class RedactSecret(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET.sub(r"\1<redacted>", message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


def redact_access_log() -> None:
    """Install the filter on the access logger, once."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, RedactSecret) for f in logger.filters):
        logger.addFilter(RedactSecret())
