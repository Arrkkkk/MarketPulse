"""Per-request latency recording.

Labels by route template (`/v1/history/{symbol}`) rather than by the
concrete path, so AAPL and MSFT accumulate into one series instead of
producing a new label per symbol — which would make the percentiles
meaningless and leak memory without bound.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from marketpulse.platform.metrics import get_metrics


def route_label(request: Request) -> str:
    """The full route template for the matched route.

    FastAPI's lazy router inclusion leaves `route.path` un-prefixed — a
    request to `/v1/overview` reports a template of `/overview`, which would
    collide across API versions. The prefix is recovered by segment count:
    whatever the concrete path has that the template does not, is prefix.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    actual = request.url.path
    if not template:
        return actual

    actual_parts = [p for p in actual.split("/") if p]
    template_parts = [p for p in template.split("/") if p]
    if len(template_parts) > len(actual_parts):
        return template

    prefix = actual_parts[: len(actual_parts) - len(template_parts)]
    return "/" + "/".join([*prefix, *template_parts])


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        metrics = get_metrics()
        metrics.observe(f"{request.method} {route_label(request)}", elapsed_ms)
        metrics.increment(f"status_{response.status_code // 100}xx")

        # Visible in a browser's network panel without opening /metrics.
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        return response
