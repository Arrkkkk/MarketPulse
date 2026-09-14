"""Mapping domain errors onto HTTP, and one error shape for every endpoint.

The provider taxonomy already distinguishes "does not exist" from "we could
not look". This module is where that distinction becomes a status code, so a
client can act on it without parsing prose.

    SymbolNotFound        -> 404  the thing is not there
    RateLimited           -> 429  we asked too often; Retry-After when known
    ProviderNotConfigured -> 503  an operator has to fix this
    CircuitOpenError      -> 503  we are deliberately not asking right now
    ProviderUnavailable   -> 502  the upstream failed

Envelope shape follows ZhuLinsen/daily_stock_analysis `api/v1/errors.py`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from marketpulse.platform.http import CircuitOpenError
from marketpulse.platform.telemetry import current_request_id
from marketpulse.providers.errors import (
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    SymbolNotFound,
)
from marketpulse.schema.api import ErrorBody

logger = logging.getLogger("marketpulse.api.errors")


def _debug_enabled() -> bool:
    """Only attach internals when the operator has asked to see them."""
    return logger.isEnabledFor(logging.DEBUG)


def error_response(
    status_code: int,
    error: str,
    message: str,
    *,
    detail: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        error=error,
        message=message,
        detail=detail if _debug_enabled() else None,
        request_id=current_request_id(),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def provider_error_response(exc: ProviderError | CircuitOpenError) -> JSONResponse:
    """Translate one domain error into its HTTP form."""
    if isinstance(exc, SymbolNotFound):
        return error_response(
            404, "symbol_not_found",
            "That symbol could not be found, or has no data for the requested range.",
            detail=str(exc),
        )
    if isinstance(exc, RateLimited):
        headers = {}
        if exc.retry_after:
            headers["Retry-After"] = str(int(exc.retry_after))
        return error_response(
            429, "rate_limited",
            "The upstream data provider is rate-limiting us. Try again shortly.",
            detail=str(exc), headers=headers or None,
        )
    if isinstance(exc, ProviderNotConfigured):
        return error_response(
            503, "provider_not_configured",
            "This feature needs an API key that is not configured on the server.",
            detail=str(exc),
        )
    if isinstance(exc, CircuitOpenError):
        return error_response(
            503, "provider_circuit_open",
            "That data provider is failing, so requests to it are paused. "
            "Service will resume automatically.",
            detail=str(exc),
            headers={"Retry-After": str(max(1, int(exc.seconds_until_half_open)))},
        )
    if isinstance(exc, ProviderUnavailable):
        return error_response(
            502, "provider_unavailable",
            "The upstream data provider could not be reached.",
            detail=str(exc),
        )
    return error_response(
        502, "provider_error", "The data provider returned an error.", detail=str(exc)
    )


def install_exception_handlers(app) -> None:
    """Register handlers so no endpoint has to write try/except itself."""

    @app.exception_handler(ProviderError)
    async def _provider(_request: Request, exc: ProviderError) -> JSONResponse:
        logger.warning("provider error: %s", exc)
        return provider_error_response(exc)

    @app.exception_handler(CircuitOpenError)
    async def _circuit(_request: Request, exc: CircuitOpenError) -> JSONResponse:
        logger.warning("circuit open: %s", exc)
        return provider_error_response(exc)
