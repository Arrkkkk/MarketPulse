"""Provider error taxonomy.

The old code answered every failure with `except Exception: return {}`, which
made "NewsAPI returned 429" indistinguishable from "this company has no news"
— and the UI then told the user, falsely, that no news existed.

These types exist so callers can tell the difference, and so the UI can say
something true. Which exception a provider raises is part of its contract and
is asserted by the shared contract test.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class. Carries which provider failed."""

    def __init__(self, message: str, *, provider: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class ProviderUnavailable(ProviderError):
    """The upstream could not be reached, or returned a server error.

    Transient by assumption: worth retrying, and worth tripping a breaker on.
    """


class RateLimited(ProviderUnavailable):
    """The upstream refused because we have exceeded a quota.

    A subclass of ProviderUnavailable so existing handling still catches it,
    but distinguishable when the UI wants to say "try again in a minute"
    rather than "the service is down".
    """

    def __init__(self, message: str, *, provider: str, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message, provider=provider)


class SymbolNotFound(ProviderError):
    """The symbol does not exist, or the upstream has no data for it.

    Permanent for this input. Never retried — a ticker that does not exist
    will not start existing because we asked again.
    """


class ProviderNotConfigured(ProviderError):
    """The credential this provider needs is absent.

    Not a failure of the upstream, and not retryable: the operator has to fix
    it. Raised at call time rather than import time so the rest of the app
    still runs without every key present.
    """
