# 9. In-process rate limiting

**Status:** Accepted, with a known limit

## Context

The AI endpoints call a paid model. A loop against `/v1/insights` spends the
operator's money, and nothing prevented it.

The usual answers are slowapi, or a Redis-backed counter shared across
replicas.

## Decision

Per-client token buckets held in process, built on the bucket already in
`platform/http.py`. Two tiers: AI endpoints at 0.2/s burst 10, everything
else at 10/s burst 60, `/health` exempt.

## Rationale

The bucket was already written and tested; `try_acquire` was the only piece
missing. Per-route-class tiers fall out naturally, and the rejection shares
the error envelope used everywhere else.

The inbound semantics differ from outbound deliberately. `acquire()` blocks,
which is right for an outbound call we still want to happen — just later.
For an inbound call it is wrong: holding the connection consumes a worker
and is indistinguishable from the service being slow. Inbound rejects with
429 and a `Retry-After`.

## Consequences

**This does not work across replicas.** Each process gets its own allowance,
so N replicas means N times the intended limit. A shared Redis bucket is
required the moment this is scaled horizontally. It is not pre-built,
because there is currently one process, and an unused Redis dependency is a
thing to operate rather than a feature.

`X-Forwarded-For` is honoured only under `MARKETPULSE_TRUST_PROXY=1`.
Without a proxy in front, the header is trivially forged and a caller could
mint a fresh identity per request; a test sprays 90 forged addresses and
asserts the limit still bites.

The client table is bounded, so a spray of forged addresses cannot grow
memory without limit.
