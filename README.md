# MarketPulse

Real-time stock and cryptocurrency dashboard with news aggregation, built as
a FastAPI service with a Streamlit client.

```
Browser ──► Streamlit UI ──httpx──► FastAPI service ──► providers ──► upstreams
            (presentation)          (routing, DI,        (yfinance,    (Yahoo,
                                     validation,          CoinGecko,    CoinGecko,
                                     error envelope)      NewsAPI,      NewsAPI,
                                                          MarketAux)    MarketAux)
                                            │
                                    platform layer
                            (retry · circuit breaker · rate limit
                             · memory→SQLite cache · logging)
```

## Quick start

```bash
uv sync
cp .env.example .env          # optional: API keys enable news and AI

# two processes
uv run uvicorn marketpulse.api.main:app --reload    # http://localhost:8000/docs
uv run streamlit run app.py                         # http://localhost:8501
```

Or with Docker:

```bash
docker compose up --build     # UI on :8501, API on :8000
```

Every credential is optional. With none set the price dashboard works fully;
news and AI report themselves as unconfigured rather than failing silently.

## Design

**Empty is not the same as failed.** Every provider raises a typed error
(`ProviderUnavailable`, `RateLimited`, `SymbolNotFound`,
`ProviderNotConfigured`) on infrastructure failure, and returns an empty
result only when the data genuinely does not exist. The API maps those onto
502/429/404/503 with a stable `error` code, and the UI says something true
for each. A rate-limited news provider no longer renders as "no news found".

**Caching is a decorator over the provider interface**, not something each
provider implements: `CachedPriceProvider(YFinanceProvider())`. Tiers run
memory → SQLite with version-prefixed keys and per-data-class TTLs. Only
successes are cached — caching a failure turns a 30-second outage into a
12-hour one.

**Outbound calls go through one resilience layer** (`platform/http.py`):
jittered exponential backoff, a circuit breaker whose half-open state admits
exactly one probe, and a per-provider token bucket sized to each free tier.

**AI output is a schema, not prose.** `NewsAnalysis` constrains the model to
a sentiment enum, a bounded confidence, themes and risk flags, so sentiment
becomes chartable data and the AI feature becomes testable — you assert on
the contract, never on wording. A response that fails validation is re-asked
once with the validation error attached; a model that is unavailable falls
back to a different one. Analyses are cached for six hours, keyed on the
article URLs, and every result carries its model, token count and estimated
cost.

**News content is treated as hostile.** Article titles and descriptions are
attacker-controlled: anyone who can get a story indexed can put text in
front of the model. Content is escaped and delimited, instructions come
after the data, and the structured schema bounds what a successful injection
could achieve. See `marketpulse/ai/prompts.py`.

**Currency comes from the ticker suffix**, not from a per-symbol metadata
lookup. The batch price download carries no currency, and fetching 29
profiles to read one field each is what made the original cold start 45s.
An unknown venue renders no currency label rather than a wrong one.

## Layout

```
marketpulse/
├── platform/     http resilience, tiered cache, LTTB downsampling, metrics
├── schema/       domain + wire models, exchange reference data
├── providers/    Protocols, typed errors, 4 providers, caching wrappers
├── services/     market orchestration, news routing
├── ai/           model registry, hardened prompts, structured analysis
├── api/          FastAPI app, DI, error envelope, middleware, v1 routers
├── client/       typed httpx client — the UI's only backend import
└── ui/           Streamlit presentation, components
```

The dependency arrow points inward only: `ui → client → api → services →
providers → platform`. Nothing in `marketpulse/` outside `ui/` imports
Streamlit.

## Development

```bash
uv run pytest                     # 299 tests, no network
uv run pytest --cov               # coverage
uv run ruff check marketpulse/
uv run python scripts/smoke_live.py    # hits real upstreams
uv run python scripts/smoke_ui.py      # drives the UI against a running API
uv run python scripts/check_models.py  # probes every catalog model (needs a key)
uv run python scripts/eval_injection.py # live prompt-injection eval (needs a key)
```

## Measured

Cold start of the 29-symbol overview, same machine and network, against the
pre-refactor baseline of **45.5s**:

| | Time | |
|---|---:|---|
| Cold (empty cache) | 1.38s | **33×** |
| Warm (in process) | 0.057s | 804× |
| Warm (new process, SQLite tier) | 0.016s | what a second visitor pays |

API latency, measured over the live service:

| Endpoint | p50 | p95 |
|---|---:|---:|
| `GET /v1/overview` (warm) | 14ms | 24ms |
| `GET /v1/analysis/{symbol}` (cached) | 2.5ms | — |
| `GET /v1/analysis/{symbol}` (cold, real model) | 2.6s | — |

Chart payload for `IBM period=max` — 16,283 rows, ~1.4MB before:

| | Bars | Raw | Gzipped |
|---|---:|---:|---:|
| Default | 800 | 110KB | **38KB** |
| `max_points=2000` | 2,000 | 275KB | 91KB |

That is ~37× smaller on the wire than the original, and the reduction is
LTTB rather than a stride, so spikes and crashes survive it.

Cache pre-warms on startup (1.7s in the background), so the first visitor
after a deploy does not pay the cold fetch. Live cache hit rate after a
browsing session: 95%.

Re-measure with `scripts/smoke_live.py` and `GET /metrics`.

## API

`GET /health` · `/health/ready` · `/info` · `/metrics`
`GET /v1/overview` · `/v1/history/{symbol}` · `/v1/profile/{symbol}` ·
`/v1/crypto/history/{coin_id}` · `/v1/news/{symbol}` · `/v1/news?q=`
`GET /v1/analysis/{symbol}` · `/v1/analysis/{symbol}/stream` (SSE) ·
`POST /v1/insights` (SSE)

Interactive docs at `/docs`.

## Configuration

| Variable | Effect when unset |
|---|---|
| `GEMINI_API_KEY` | AI analysis reports as unconfigured |
| `GEMINI_MODEL` | Falls back to the catalog default in `marketpulse/ai/models.json` |
| `NEWS_API_KEY` | US-listing news falls back to MarketAux |
| `MARKETAUX_API_KEY` | Non-US news falls back to NewsAPI |
| `MARKETPULSE_API_URL` | UI defaults to `http://localhost:8000` |
| `LOG_LEVEL` | `INFO` |

## Verified against the live API

`scripts/check_models.py` calls every catalog model with a real
`response_schema`, because listing is not enough — `gemini-2.5-flash`
appears in `models.list()` for this key and returns 404 when invoked. All
five catalog entries answered with a valid schema on 2026-09-15.

`scripts/eval_injection.py` is the behavioural half of the prompt-injection
defence. Unit tests assert the prompt's structure; only a live model can
tell you whether an attack works. Six attacks — direct override, a forged
system turn, fake regulatory authority, role reassignment, a delimiter
flood, and a base64-encoded instruction — were added to a set of
unambiguously bearish articles. All six failed to move the verdict off
bearish, and all six were reported in `risk_flags`.

## Still unverified

- **Per-token pricing.** Rates in `marketpulse/ai/models.json` are `null`.
  Token counts come back exact from the API and are always reported; cost is
  reported only once someone fills in rates confirmed against
  <https://ai.google.dev/pricing>. A wrong rate is worse than no rate.
- **The Docker build.** `Dockerfile` and `compose.yaml` are written and the
  compose file parses, but Docker is not installed on the machine this was
  developed on, so `docker compose up` has never been executed.
