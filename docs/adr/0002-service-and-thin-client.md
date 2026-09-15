# 2. FastAPI service with a thin Streamlit client

**Status:** Accepted

## Context

`app.py` was 542 lines mixing UI, orchestration, network I/O, caching and AI
calls at module scope. Nothing could be tested without starting Streamlit,
and nothing could be reused by anything that was not a browser.

Three options: keep one process and enforce layers inside it; split into a
service plus a thin client; or rewrite the frontend in Next.js.

## Decision

Split. FastAPI owns providers, caching, resilience and AI. Streamlit becomes
presentation, reaching the backend only through a typed client.

`ui → client → api → services → providers → platform`, never upward.

## Consequences

`app.py` is 11 lines. The service is independently testable, deployable and
documented, and gets an OpenAPI spec for free. A future Next.js frontend or
MCP server is a client change, not a rewrite.

The cost is two processes to run, which `compose.yaml` absorbs.

The Next.js option was rejected because the frontend was not the problem —
the absence of a backend was. Rewriting the UI would have been the most
visible change and the least useful one.

The layering is enforced by three `import-linter` contracts in CI rather
than by convention, because a convention this load-bearing will rot.
