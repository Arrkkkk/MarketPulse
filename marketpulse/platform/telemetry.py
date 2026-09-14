"""Logging.

One place to create loggers, so no module reaches for `print()`. Structured
JSON output and request-id correlation land in Phase 9; the interface here is
already the one those will fill in, so call sites will not need to change.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)-34s %(message)s"


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
