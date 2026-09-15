# 7. LTTB rather than stride downsampling

**Status:** Accepted

## Context

`IBM period=max` is 16,283 daily bars, about 1.4MB of JSON. The original UI
requested it and rendered every point, on every rerun.

The first fix took every Nth row. That bounds the payload correctly.

## Decision

Largest-Triangle-Three-Buckets (Steinarsson, 2013). Default 800 points.

## Rationale

A stride bounds bytes and lies about shape. With 16,000 bars reduced to 800,
the stride is 20 — so a crash and the spike before it, six samples apart,
can both fall between sampled points. The chart then looks calm through the
most important week in the series.

For price data that is not a cosmetic difference. It is the wrong answer.

LTTB keeps the point count fixed and chooses *which* points by triangle
area, so extremes survive and flat stretches collapse. The test that
justifies the change builds a flat series with one spike and one crash and
asserts LTTB keeps both while a stride provably misses them.

## Consequences

Selection runs on `close` and whole rows are kept, so every returned bar is
a real bar rather than an interpolation — open, high, low and volume stay
consistent with the close that was selected.

`total` is reported alongside `count`, so a client can tell it is looking at
a reduced series.

Combined with gzip: 16,283 rows and ~1.4MB became 800 bars and 38KB, about
37× smaller on the wire, with the shape intact.
