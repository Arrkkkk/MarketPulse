"""Typed client for the MarketPulse service — the UI's only backend import."""

from marketpulse.client.client import (  # noqa: F401
    DEFAULT_BASE_URL,
    MarketPulseClient,
    MarketPulseClientError,
    bars_to_frame,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "MarketPulseClient",
    "MarketPulseClientError",
    "bars_to_frame",
]
