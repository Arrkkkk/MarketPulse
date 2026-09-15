# 5. The platform layer is synchronous

**Status:** Accepted

## Context

FastAPI is an async framework, and the reference implementation this layer
was adapted from (`wshobson/maverick-mcp`) is async throughout.

## Decision

`platform/http.py` and `platform/cache.py` are synchronous, guarded by
`threading.Lock`.

## Rationale

Every upstream client here blocks: `yfinance`, `pycoingecko` and `requests`
have no async interface. Wrapping blocking calls in async functions does not
make them yield — it moves the blocking into the event loop, where it is
worse, and requires `run_in_executor` to undo.

FastAPI runs `def` endpoints in a threadpool automatically, so synchronous
handlers compose correctly without any of that. Concurrency where it matters
(fetching stocks and crypto together) uses a `ThreadPoolExecutor`.

## Consequences

Endpoints are `def`, not `async def`, and are served from the threadpool.

The one place this shows is streaming: `StreamingResponse` takes a sync
generator, which Starlette iterates in a threadpool. Fine at this scale;
worth revisiting if concurrent streaming connections ever become the
bottleneck.

Switching to an async HTTP client later would mean rewriting this layer. The
cost of that is bounded and visible; the cost of pretending blocking calls
are async is neither.
