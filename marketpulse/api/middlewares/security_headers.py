"""Response security headers.

This API serves JSON and SSE to a Streamlit client, not HTML to a browser,
so the threat surface is narrow — but the headers are close to free and
close the cases that do apply: a browser sniffing a JSON response into
something executable, an error page framed by another site, and a referrer
carrying a symbol or query string to a third party.

HSTS is deliberately not set here. It belongs at whatever terminates TLS,
and asserting it from a service reached over plain HTTP in development
would pin the developer's browser to https://localhost.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADERS = {
    # Stop a browser reinterpreting application/json as HTML or a script.
    "X-Content-Type-Options": "nosniff",
    # Nothing here is meant to be framed.
    "X-Frame-Options": "DENY",
    # Do not leak the path — which contains the symbol being viewed — to
    # any third party a response might link out to.
    "Referrer-Policy": "no-referrer",
    # The API returns no HTML, so nothing needs to be loadable at all.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cross-Origin-Resource-Policy": "same-site",
    # This service has no camera, microphone or geolocation use.
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in HEADERS.items():
            # Never clobber a header a handler set deliberately.
            response.headers.setdefault(header, value)
        return response
