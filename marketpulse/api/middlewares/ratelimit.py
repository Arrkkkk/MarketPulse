"""Per-client inbound rate limiting.

Two different costs need two different limits. A cached `/v1/overview` is
essentially free and should be generous. `/v1/analysis` and `/v1/insights`
call a paid model, and a loop against them spends the operator's money —
so they get a much tighter bucket.

Built on the token bucket already in `platform.http` rather than adding
slowapi. The bucket is written and tested, `try_acquire` is the only piece
it was missing, and per-route-class tiers fall out naturally. The tradeoff
is that this is in-process: with more than one replica each gets its own
allowance, so a shared Redis bucket becomes necessary the moment the
service is scaled horizontally. That is noted rather than pre-built,
because there is currently one process.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from marketpulse.platform.http import RateLimiter
from marketpulse.platform.telemetry import current_request_id, get_logger

logger = get_logger("api.ratelimit")


@dataclass(frozen=True)
class Tier:
    name: str
    rate_per_second: float
    burst: int


#: Costs a model call. A page that analyses one symbol needs two of these
#: (the stream plus the structured read), so the burst allows a handful of
#: symbols in quick succession and then throttles hard.
AI_TIER = Tier("ai", rate_per_second=0.2, burst=10)

#: Everything else. Cached responses are cheap, and the dashboard fires
#: several requests per page load, so this only catches genuine abuse.
DEFAULT_TIER = Tier("default", rate_per_second=10.0, burst=60)

#: Never limited: an orchestrator polling /health must not be throttled into
#: reporting the service as down.
EXEMPT_PATHS = frozenset({"/health", "/health/ready"})

#: Bound on distinct clients tracked, so the table cannot grow without limit
#: under a spray of forged addresses.
MAX_TRACKED_CLIENTS = 10_000


def client_key(request: Request) -> str:
    """Identify the caller.

    X-Forwarded-For is honoured only when the service is told it is behind a
    proxy, because the header is trivially forged when it is not. Without
    that flag a caller could mint a fresh identity per request and bypass
    the limit entirely.
    """
    if getattr(request.app.state, "trust_proxy_headers", False):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def is_ai_path(path: str) -> bool:
    """Whether this path costs a model call."""
    return "/analysis" in path or path.endswith("/insights")


def tier_for(path: str) -> Tier:
    """The production tier for a path."""
    return AI_TIER if is_ai_path(path) else DEFAULT_TIER


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        enabled: bool = True,
        default_tier: Tier | None = None,
        ai_tier: Tier | None = None,
    ) -> None:
        """Tiers are injectable so tests can pin exact limits.

        Asserting on the production tiers means asserting that 90 requests
        outrun a bucket refilling at 10/s — which is true on a fast machine
        and false on a slow one. That test passed locally every time and
        failed in CI.
        """
        super().__init__(app)
        self.enabled = enabled
        self._default_tier = default_tier or DEFAULT_TIER
        self._ai_tier = ai_tier or AI_TIER
        self._buckets: dict[tuple[str, str], RateLimiter] = {}
        self._lock = threading.Lock()

    def tier(self, path: str) -> Tier:
        return self._ai_tier if is_ai_path(path) else self._default_tier

    def _bucket(self, key: str, tier: Tier) -> RateLimiter:
        with self._lock:
            if len(self._buckets) >= MAX_TRACKED_CLIENTS:
                # Full. Dropping the table costs everyone one free burst,
                # which is strictly better than unbounded memory growth.
                logger.warning("rate-limit table full; clearing %d entries", len(self._buckets))
                self._buckets.clear()
            bucket = self._buckets.get((key, tier.name))
            if bucket is None:
                bucket = RateLimiter(tier.rate_per_second, burst=tier.burst)
                self._buckets[(key, tier.name)] = bucket
            return bucket

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self.enabled or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        tier = self.tier(request.url.path)
        bucket = self._bucket(client_key(request), tier)

        if not bucket.try_acquire():
            retry_after = max(1, int(bucket.seconds_until_available()) + 1)
            logger.warning(
                "rate limited %s on %s (tier=%s)",
                client_key(request),
                request.url.path,
                tier.name,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": (
                        "Too many requests. This endpoint is limited to protect "
                        "upstream quotas and AI spend."
                        if is_ai_path(request.url.path)
                        else "Too many requests."
                    ),
                    "detail": None,
                    "request_id": current_request_id(),
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
