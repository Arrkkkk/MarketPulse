"""The FastAPI application.

    uv run uvicorn marketpulse.api.main:app --reload

Middleware order is outermost-first, and it matters. ErrorHandler wraps
everything so nothing escapes as a stack trace. RequestId sits inside it so
an id exists before any handler runs and appears in the error body.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from marketpulse.api.errors import install_exception_handlers
from marketpulse.api.middlewares import (
    ErrorHandlerMiddleware,
    RateLimitMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
)
from marketpulse.api.middlewares.ratelimit import Tier
from marketpulse.api.v1.health import VERSION
from marketpulse.api.v1.router import api_router, meta_router
from marketpulse.config import get_settings
from marketpulse.platform.telemetry import RequestIdFilter, get_logger
from marketpulse.services.factory import build_market_service

logger = get_logger("api")

DESCRIPTION = """
Real-time stock and cryptocurrency market data with news aggregation.

Every endpoint distinguishes *no data* from *could not fetch*: an empty
result means the data genuinely does not exist, while an upstream failure
returns 502, 503 or 429 with a machine-readable `error` code.
"""


def _prewarm() -> None:
    """Populate the cache before the first visitor arrives.

    Without this the first person to load the dashboard after a deploy pays
    the full cold fetch while everyone after them gets a warm cache. Runs on
    a daemon thread so a slow or failing upstream delays neither startup nor
    shutdown, and failure is logged rather than raised — a warm cache is an
    optimisation, not a prerequisite.
    """
    try:
        started = time.perf_counter()
        overview = build_market_service().get_overview()
        logger.info(
            "cache pre-warmed in %.2fs (%d stocks, %d crypto)",
            time.perf_counter() - started,
            len(overview.stocks),
            len(overview.crypto),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache pre-warm failed (%s); first request will be cold", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    missing = settings.missing_credentials()
    if missing:
        # Warn, do not refuse to start. The price dashboard works with no
        # credentials at all; each missing key disables one feature.
        logger.warning(
            "starting with %d credential(s) unset: %s — the matching features "
            "will report as unconfigured",
            len(missing),
            ", ".join(missing),
        )
    if os.environ.get("MARKETPULSE_PREWARM", "1") != "0":
        threading.Thread(target=_prewarm, name="prewarm", daemon=True).start()

    logger.info("marketpulse api %s ready", VERSION)
    yield
    logger.info("marketpulse api shutting down")


def create_app(rate_limit_tiers: tuple[Tier, Tier] | None = None) -> FastAPI:
    """Application factory — tests build their own instance.

    `rate_limit_tiers` is (default, ai). Overridden only by tests, which
    need limits small enough to hit deterministically rather than limits
    that depend on how fast the machine runs the loop.
    """
    app = FastAPI(
        title="MarketPulse API",
        description=DESCRIPTION,
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Whether X-Forwarded-For can be trusted. Off by default: the header is
    # trivially forged when there is no proxy in front, which would let a
    # caller mint a new rate-limit identity per request.
    app.state.trust_proxy_headers = os.environ.get("MARKETPULSE_TRUST_PROXY") == "1"

    # Starlette's add_middleware PREPENDS, so the LAST one added ends up
    # outermost. They are therefore registered inner-to-outer below, which
    # reads backwards and is the whole reason this comment exists — an
    # earlier version listed them outer-to-inner and produced exactly the
    # inverse stack: 429s carried no request id, ErrorHandler could not see
    # failures in the middleware above it, and rejected requests were being
    # counted as served latency.
    #
    # Effective order, outermost to innermost:
    #   CORS -> ErrorHandler -> SecurityHeaders -> RequestId -> RateLimit
    #        -> Timing -> GZip -> router

    # OHLCV JSON is repetitive and compresses about 3:1. Innermost, so the
    # timing above it includes the cost of compressing.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Inside RateLimit: a rejected request was never served and should not
    # appear in served-latency percentiles.
    app.add_middleware(TimingMiddleware)

    # Inside RequestId, so a 429 body carries a request id like every other
    # error response.
    default_tier, ai_tier = rate_limit_tiers or (None, None)
    app.add_middleware(
        RateLimitMiddleware,
        enabled=os.environ.get("MARKETPULSE_RATELIMIT", "1") != "0",
        default_tier=default_tier,
        ai_tier=ai_tier,
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # Outside everything it protects, so an exception anywhere below still
    # becomes a clean JSON error rather than a stack trace.
    app.add_middleware(ErrorHandlerMiddleware)

    # Outermost: CORS headers must reach error responses too, and a
    # preflight should be answered without waking anything below.
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins, not "*". The Streamlit UI is the only browser
        # client; widen this deliberately if that changes.
        allow_origins=[
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://ui:8501",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)
    app.include_router(meta_router)
    app.include_router(api_router)

    # Attach the request id to every log record the app emits.
    logging.getLogger("marketpulse").addFilter(RequestIdFilter())
    return app


app = create_app()
