"""Catch-all for anything the typed handlers did not.

FastAPI's exception handlers cover the domain errors we know about. This
middleware exists for the ones we do not: it guarantees that no request ever
returns a bare stack trace or an HTML error page, and that every unhandled
failure is logged with the path, method and traceback.

Adapted from ZhuLinsen/daily_stock_analysis `api/middlewares/error_handler.py`.
"""

from __future__ import annotations

import logging
import traceback

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from marketpulse.api.errors import error_response

logger = logging.getLogger("marketpulse.api")


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 — that is the point of this class
            logger.error(
                "unhandled exception\n  path: %s\n  method: %s\n%s",
                request.url.path,
                request.method,
                traceback.format_exc(),
            )
            return error_response(
                500,
                "internal_error",
                "Something went wrong on our side. The request id identifies this failure.",
                detail=str(exc),
            )
