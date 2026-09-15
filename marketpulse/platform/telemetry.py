"""Logging and request correlation.

Two output formats, chosen by `LOG_FORMAT`:

    text  (default)  human-readable, for a terminal
    json             one object per line, for anything that ingests logs

JSON rather than structlog. Every call site here already uses stdlib
`%`-style logging, and converting several hundred of them to key-value calls
would be a large mechanical change for a modest gain. A formatter gets the
part that actually matters — machine-parseable lines carrying the request id
— and `extra={...}` adds real structured fields at the call sites where
structure is worth having.

The request id lives here rather than in the HTTP middleware that sets it,
so any layer can stamp a log line without importing the API package.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

_CONFIGURED = False

#: The handler this module installs. Tracked so reconfiguration removes
#: only its own handler — clearing the whole list would also destroy a
#: handler an operator (or a test) attached deliberately.
_OUR_HANDLER: logging.Handler | None = None

TEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)-34s %(message)s"

#: Placeholder for a line emitted outside any request. The text format needs
#: *something* printable in the column; JSON wants a real null. Shared here so
#: the two formatters cannot disagree about what "no id" looks like.
NO_REQUEST_ID = "-"

#: Attributes LogRecord always carries. Anything else on the record came
#: from an `extra=` and is a field worth emitting.
_STANDARD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        "request_id",
    }
)

# --- request correlation ---------------------------------------------------

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
        record.request_id = current_request_id() or NO_REQUEST_ID
        return True


# --- formatting ------------------------------------------------------------


def _json_request_id(record: logging.LogRecord) -> str | None:
    """The id, or null — never the text format's dash placeholder."""
    value = getattr(record, "request_id", None)
    return None if not value or value == NO_REQUEST_ID else value


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Timestamps are RFC 3339 in UTC so they sort lexicographically and carry
    no ambiguity about the host's timezone.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _json_request_id(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed via extra=. This is where structure comes from.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                payload[key] = value

        # default=str so a stray object cannot take the process down by
        # being unserializable — a log line must never raise.
        return json.dumps(payload, default=str, ensure_ascii=False)


def _configure_once() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RequestIdFilter())

    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    global _OUR_HANDLER
    root = logging.getLogger("marketpulse")
    root.setLevel(getattr(logging, level, logging.INFO))
    if _OUR_HANDLER is not None and _OUR_HANDLER in root.handlers:
        root.removeHandler(_OUR_HANDLER)
    root.addHandler(handler)
    _OUR_HANDLER = handler
    # Do not also emit through the root logger, which would double every line
    # once uvicorn installs its own handler.
    root.propagate = False
    _CONFIGURED = True


def reset_logging() -> None:
    """Force reconfiguration. For tests that switch LOG_FORMAT."""
    global _CONFIGURED, _OUR_HANDLER
    _CONFIGURED = False
    root = logging.getLogger("marketpulse")
    if _OUR_HANDLER is not None and _OUR_HANDLER in root.handlers:
        root.removeHandler(_OUR_HANDLER)
    _OUR_HANDLER = None


def get_logger(name: str) -> logging.Logger:
    """A logger under the `marketpulse` namespace."""
    _configure_once()
    if not name.startswith("marketpulse"):
        name = f"marketpulse.{name}"
    return logging.getLogger(name)
