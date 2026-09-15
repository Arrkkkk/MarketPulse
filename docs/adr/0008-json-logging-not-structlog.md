# 8. A stdlib JSON formatter, not structlog

**Status:** Accepted

## Context

Structured logging usually means structlog: key-value events, processors,
context binding.

## Decision

A `logging.Formatter` subclass emitting one JSON object per line, selected
by `LOG_FORMAT=json`. Text remains the default.

## Rationale

Every call site in this codebase already uses stdlib `%`-style logging.
Converting several hundred of them to key-value calls is a large mechanical
change, and the part that actually matters for operating the service is
machine-parseable lines carrying the request id — which a formatter gives
directly.

`extra={...}` supplies real structured fields where structure is worth
having, such as the analysis-complete line carrying symbol, model,
sentiment, confidence, token counts and latency as first-class keys.

## Consequences

Log lines are JSON-wrapped messages plus explicit fields, rather than pure
event streams. For a two-service application ingested by anything that reads
JSON lines, that distinction does not pay for the migration.

Two details are deliberate:

- `json.dumps(..., default=str)`, because a log line must never be able to
  take the process down by being handed an unserializable object.
- The "no request id" sentinel is a shared constant. The text format needs
  something printable in its column and JSON wants a real null; when those
  two disagreed, lines outside a request serialised the dash.
