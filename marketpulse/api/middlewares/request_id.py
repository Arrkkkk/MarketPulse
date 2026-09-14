"""Request correlation middleware.

Assigns an id to every request and returns it as `X-Request-ID`, so a user
can quote it in a bug report and an operator can find that exact request.

The ContextVar itself lives in `platform.telemetry` — any layer may stamp a
log line with the id, and only this middleware knows about HTTP.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from marketpulse.platform.telemetry import reset_request_id, set_request_id

HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns an id per request, honouring a sane one supplied by the caller."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(HEADER)
        # Accept a caller-supplied id only if it looks like one. Otherwise a
        # caller controls a value that lands in our logs.
        if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum():
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex

        token = set_request_id(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers[HEADER] = request_id
        return response
