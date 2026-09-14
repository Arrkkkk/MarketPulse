"""The FastAPI application.

    uv run uvicorn marketpulse.api.main:app --reload

Middleware order is outermost-first, and it matters. ErrorHandler wraps
everything so nothing escapes as a stack trace. RequestId sits inside it so
an id exists before any handler runs and appears in the error body.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from marketpulse.api.errors import install_exception_handlers
from marketpulse.api.middlewares import ErrorHandlerMiddleware, RequestIdMiddleware
from marketpulse.api.v1.health import VERSION
from marketpulse.api.v1.router import api_router, meta_router
from marketpulse.config import get_settings
from marketpulse.platform.telemetry import RequestIdFilter, get_logger

logger = get_logger("api")

DESCRIPTION = """
Real-time stock and cryptocurrency market data with news aggregation.

Every endpoint distinguishes *no data* from *could not fetch*: an empty
result means the data genuinely does not exist, while an upstream failure
returns 502, 503 or 429 with a machine-readable `error` code.
"""


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
    logger.info("marketpulse api %s ready", VERSION)
    yield
    logger.info("marketpulse api shutting down")


def create_app() -> FastAPI:
    """Application factory — tests build their own instance."""
    app = FastAPI(
        title="MarketPulse API",
        description=DESCRIPTION,
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Outermost first.
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins, not "*". The Streamlit UI is the only browser
        # client; widen this deliberately if that changes.
        allow_origins=[
            "http://localhost:8501",
            "http://127.0.0.1:8501",
            "http://ui:8501",
        ],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)
    app.include_router(meta_router)
    app.include_router(api_router)

    # Attach the request id to every log record the app emits.
    logging.getLogger("marketpulse").addFilter(RequestIdFilter())
    return app


app = create_app()
