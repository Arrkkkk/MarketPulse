"""Logging.

One place to create loggers, so no module reaches for `print()`. Structured
JSON output and request-id correlation land in Phase 9; the interface here is
already the one those will fill in, so call sites will not need to change.
"""

from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)-34s %(message)s"

# --- request correlation ---------------------------------------------------
# The id lives here rather than in the HTTP middleware that sets it, so that
# any layer can stamp a log line with it without importing the API package.
# (Putting it in the middleware created an import cycle: errors -> middleware
# -> errors.)

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    """The id of the request being handled on this task, if any."""
    return _request_id.get()


def set_request_id(request_id: str):
    """Bind an id for the current context. Returns a token for `reset`."""
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every record so formatters can include it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        return True


def _configure_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    root = logging.getLogger("marketpulse")
    root.setLevel(getattr(logging, level, logging.INFO))
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """A logger under the `marketpulse` namespace."""
    _configure_once()
    if not name.startswith("marketpulse"):
        name = f"marketpulse.{name}"
    return logging.getLogger(name)
