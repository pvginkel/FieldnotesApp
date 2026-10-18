"""Log formatting shared by run.py and the testing-mode handler in create_app()."""

import logging

# LogRecord attributes that are not extra fields.
_STANDARD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "relativeCreated",
        "filename",
        "module",
        "funcName",
        "levelno",
        "levelname",
        "lineno",
        "pathname",
        "exc_info",
        "exc_text",
        "stack_info",
        "thread",
        "threadName",
        "process",
        "processName",
        "taskName",
        "message",
        "asctime",
        "msecs",
    }
)


class ExtraFieldFormatter(logging.Formatter):
    """Formatter that appends the ``extra={...}`` fields of a logging call.

    The standard Formatter drops them. Here they follow the message as sorted
    ``key=value`` pairs::

        2026-04-07 15:30:00,123 [INFO] app.services.x: Item imported | item_id=42 source=csv
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)

        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if not extras:
            return base

        pairs = " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return f"{base} | {pairs}"


def resolve_log_level(level_name: str) -> int:
    """A level name such as ``debug`` or ``WARNING`` as a logging level; INFO if unknown."""
    level = getattr(logging, level_name.upper(), None)
    return level if isinstance(level, int) else logging.INFO
