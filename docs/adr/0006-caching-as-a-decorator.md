# 6. Caching decorates the provider interface

**Status:** Accepted

## Context

The original app ran two caches at once: `st.session_state` dictionaries and
`@st.cache_data`, with overlapping contents and different lifetimes. Neither
was shared between users, so every visitor paid the full upstream cost, and
the free-tier quotas were spent per-session.

The obvious alternative is to add caching inside each provider.

## Decision

Caching is a separate class implementing the same Protocol:

```python
provider = CachedPriceProvider(YFinanceProvider())
```

Tiers run memory → SQLite, with version-prefixed keys and TTLs chosen per
data class (quotes 30s, daily history 12h, profiles 24h, news 1h, AI 6h).

## Consequences

Written once, tested once, inherited by every provider. A provider stays a
thing that knows how to fetch. Tests substitute an uncached provider without
ceremony, and a cached wrapper is still substitutable for the thing it wraps
— asserted in the contract test.

Two properties are load-bearing and easy to get wrong:

- **Only successes are cached.** Caching a failure turns a brief outage into
  a long one. `get_or_set` writes nothing when the producer raises.
- **Batch requests are partially served.** A refresh where one symbol is new
  costs one batch of one, not a full refetch.

Measured: cold overview 1.38s, warm in-process 0.057s, warm from the SQLite
tier in a fresh process 0.016s. That last number is what a second visitor
pays.

Redis is the obvious third tier and is deliberately absent — see ADR 9 for
the same reasoning applied to rate limiting.
