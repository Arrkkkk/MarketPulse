# 1. Providers raise on failure; empty means empty

**Status:** Accepted

## Context

Every data function in the original codebase ended the same way:

```python
except Exception as e:
    print(f"Error fetching...: {e}")
    return {}
```

So a 429 from NewsAPI, a missing API key, and a company with genuinely no
coverage all produced the same value. The UI could not tell them apart, and
it rendered "No recent news found for AAPL" whenever we were rate-limited —
a false statement, presented to the user as fact.

## Decision

An empty result means the data genuinely does not exist. Infrastructure
failure raises:

| Situation | Response |
|---|---|
| Valid input, nothing to return | empty list or frame |
| Symbol does not exist | `SymbolNotFound` |
| Credential absent | `ProviderNotConfigured` |
| Quota exhausted | `RateLimited` |
| Network, timeout, 5xx | `ProviderUnavailable` |

`tests/test_provider_contract.py` asserts this for every implementation,
including the inverse: a healthy 200 with no articles must *return* empty,
not raise.

## Consequences

Every call site must now handle exceptions — that is the point, not a cost.
The API maps the taxonomy onto 404/429/502/503, and the UI says something
true for each. Caching inherits it: only successes are stored, so a
thirty-second outage cannot become a twelve-hour one.

The rule reaches further than the providers. `NewsService` originally
returned an empty list when no news key was configured, which rendered as
"no news found" — the same lie one level up. It raises now.

Taken from `virattt/ai-hedge-fund`, whose `data/protocol.py` states it
directly: a provider that returns empty on failure "poisons backtests,
because missing data is indistinguishable from 'no signal'".
