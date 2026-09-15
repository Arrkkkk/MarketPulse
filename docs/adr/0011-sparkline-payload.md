# 11. A sparkline on the overview, stocks only

**Status:** Accepted

## Context

A snapshot tile shows a price and a day-over-day delta. Both are single
numbers; neither answers "has this been trending, or is today the whole
story." The redesign wanted a recent-trend line on every stock tile.

The data for it already exists on the server without a new fetch:
`MarketService.get_overview()` calls `get_histories(symbols, period="1y",
interval="1d")` to build the day-over-day delta in the first place, so a
year of daily bars per symbol is already in memory by the time
`to_quote()` collapses each one down to a single price. The API handler
was discarding the other ~250 rows.

Crypto is a different shape. `get_overview()` fetches crypto through
`get_quotes()` — current prices only, never histories — because a
day-over-day delta there comes from CoinGecko's `change_percent_24h`
field directly, not from a fetched series. There is no frame to slice a
sparkline from without a new upstream call per coin.

## Decision

`Quote` gains an optional `spark: list[float] | None` field: the last 30
closes, oldest first. `PriceHistory.to_quote()` grows a keyword-only
`spark_points` parameter that populates it; `None` (the default) skips the
work entirely, so the one caller that doesn't want it — `MarketService.
get_quote()`, used by the single-symbol detail view — pays nothing. Only
`/v1/overview` passes `spark_points=30`.

`CryptoQuote` is unchanged. Crypto sparklines are out of scope for this
change, not merely deferred by omission — see Consequences.

## Rationale

**A window, not a downsample.** ADR 0007 exists because reducing 16,000
bars to 800 for a chart needs to preserve shape, which is what LTTB is
for. Thirty points pulled from a year of daily bars is a different
operation: there is nothing to preserve a shape *of*, because the point is
the tail of the series, not a representative summary of the whole thing.
Calling `frame["close"].tail(30)` says that directly; running LTTB over a
year to end up back at "the last 30 days" would say the wrong thing about
what the number means, for the same cost in code someone has to read
later.

**Population lives in `to_quote()`, not the router.** `to_quote()` already
reaches into the frame for open/high/low/volume; the sparkline is one more
field extracted the same way, from data the method already has open in
front of it. Putting it in `api/v1/market.py` instead would mean the
router reaching past the domain model into a pandas frame directly, which
is the layering this project's import-linter contracts exist to prevent
in the other direction.

**Stocks only, deliberately.** Extending this to crypto is not "the same
change, more coins" — it is a second, materially different decision: an
additional per-coin history fetch against CoinGecko's free tier, on every
overview load, for eleven coins. That has its own rate-limit and latency
cost and deserves its own ADR when someone decides it is worth taking,
rather than riding in silently on this one because the two fields happen
to share a name.

## Consequences

**Measured**, `uvicorn` warm, gzip on, same machine as the README's other
figures — before this change and after it, same watchlist:

| | Before | After | |
|---|---:|---:|---|
| `/v1/overview`, raw JSON | 8,808 B | 23,626 B | 2.7× |
| `/v1/overview`, gzipped | 2,332 B | 6,816 B | 2.9× |
| `/v1/overview` latency | ~24ms (p95, see README) | unchanged — no new I/O, one `pandas.tail()` per symbol | |

Under 7KB gzipped either way. The relative jump looks large; the absolute
number is not one a modern connection notices, and the alternative — an
additional round trip per tile to fetch a sparkline separately — would
cost far more than the 4.5KB this adds.

`spark` is `None` on every `Quote` outside the overview response,
including the single-symbol quote `get_quote()` builds for the detail
page's key-metrics block, and is entirely absent from `CryptoQuote`. A
client checks for the field rather than assuming it.

The sparkline's own text alternative is its `aria-label`, stated in words
("30-day trend: rose from 303.16 to 333.08, +9.9%") rather than a
disclosed data table the way a full chart gets one — complete for 30
points in a way it would only be partial for a year of OHLCV bars.

A future crypto sparkline, if it happens, is a new decision: it needs its
own per-coin history fetch, its own cache entry, and its own line in this
file.
