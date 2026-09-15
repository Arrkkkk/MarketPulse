# MarketPulse

[![CI](https://github.com/Arrkkkk/MarketPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Arrkkkk/MarketPulse/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Coverage 91%](https://img.shields.io/badge/coverage-91%25-brightgreen)
![Tests 448](https://img.shields.io/badge/tests-448-brightgreen)

Real-time stock and cryptocurrency dashboard with news aggregation and
AI-generated sentiment analysis, built as a FastAPI service with a thin
Streamlit client.

```
                    ┌──────────────────────────────────────────┐
   Browser ────────▶│  Streamlit UI       presentation only    │
                    └──────────────────┬───────────────────────┘
                                       │  MarketPulseClient
                                       │  typed httpx · SSE
                    ┌──────────────────▼───────────────────────┐
                    │  FastAPI service                         │
                    │   api/v1   routing · validation · errors │
                    │   services orchestration                 │
                    │   ai/      registry · prompts · analyst  │
                    └──────────────────┬───────────────────────┘
                                       │  Protocols
                    ┌──────────────────▼───────────────────────┐
                    │  providers   CachedX(X()) wrappers       │
                    └──────────────────┬───────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │  platform                                │
                    │   retry · circuit breaker · rate limit   │
                    │   memory→SQLite cache · LTTB · metrics   │
                    └──────────────────┬───────────────────────┘
                                       │
                 Yahoo Finance · CoinGecko · NewsAPI · MarketAux · Gemini
```

The dependency arrow points inward only, and three `import-linter` contracts
check it on every push — so the layering cannot rot quietly.

## Quick start

```bash
uv sync
cp .env.example .env          # optional: keys enable news and AI

uv run uvicorn marketpulse.api.main:app --reload    # http://localhost:8000/docs
uv run streamlit run app.py                         # http://localhost:8501
```

Or `docker compose up --build`. See [docs/deploying.md](docs/deploying.md).

Every credential is optional and each gates one feature. With none set the
price dashboard works fully; news and AI report themselves as unconfigured
rather than failing quietly.

## The idea it is built around

**Empty is not the same as failed.**

Every data function in the original version ended `except Exception: return {}`,
so a rate-limit error and a company with no coverage produced the same
value — and the UI told users "No recent news found" whenever we were
throttled. A false statement, presented as fact.

Now providers raise a typed error (`RateLimited`, `SymbolNotFound`,
`ProviderUnavailable`, `ProviderNotConfigured`) and return empty only when
the data genuinely is. The API maps those onto 429/404/502/503 with a stable
`error` code, and the UI says something true for each. A shared contract
test asserts it for every provider, including the inverse: a healthy
response with no results must *return* empty, not raise.

That one rule shapes most of the rest — [ADR 1](docs/adr/0001-fail-loud-provider-contract.md).

## What else is worth knowing

**Caching decorates the interface** rather than living inside each provider:
`CachedPriceProvider(YFinanceProvider())`. Memory → SQLite, version-prefixed
keys, TTLs per data class. Only successes are cached — a brief outage must
not become a twelve-hour one.

**One resilience layer** wraps every outbound call: jittered backoff, a
circuit breaker whose half-open state admits exactly one probe, and a token
bucket sized to each free tier.

**AI output is a schema, not prose.** The model must return a sentiment
enum, a bounded confidence, themes and risk flags, so sentiment is chartable
data and the feature is testable — you assert the contract, never the
wording. Invalid output is re-asked once with the validation error attached;
an unavailable model falls back to a different one.

**News content is treated as hostile.** Article text is attacker-controlled;
it is escaped and delimited, instructions come after the data, and the
schema bounds what a successful injection could achieve.

**Currency comes from the ticker suffix.** The batch price download carries
no currency, and fetching 29 profiles to read one field each is what made
the original cold start 45 seconds. An unknown venue renders no label rather
than a wrong one.

**Search by name.** The original required you to know that Reliance is
`RELIANCE.NS`. Typing `hsbc` now returns HSBC (USD), 0005.HK (HKD) and
HSBA.L (GBP).

## Measured

Against the pre-refactor baseline, same machine and network. Re-measure with
`scripts/smoke_live.py` and `GET /metrics`.

| | Before | After | |
|---|---:|---:|---|
| Cold start, 29-symbol overview | 45.5s | **1.38s** | 33× |
| Warm overview, in process | — | 0.057s | |
| Warm overview, new process | — | 0.016s | what a second visitor pays |
| `GET /v1/overview` p95 | — | **24ms** | |
| `IBM period=max` on the wire | ~1.4MB | **38KB** | 37×, gzipped, shape intact |
| Symbols resolving | 27/29 | **29/29** | two were delisted |
| Cached AI analysis | — | 2.5ms | vs 2.6s cold |
| Cache hit rate, browsing session | — | 95% | |
| Tests | 0 | **448** | 91% covered, no network |

The 45.5s figure decomposed as 21s of `.info` calls, 14.6s of hardcoded
`sleep(0.5)`, and 9.9s of actual fetching, one symbol at a time.

## Verified against the live API

`scripts/check_models.py` calls every catalog model with a real
`response_schema`, because listing is not enough — `gemini-2.5-flash`
appears in `models.list()` and returns 404 when invoked.

`scripts/eval_injection.py` is the behavioural half of the injection
defence, which unit tests structurally cannot cover. Six attacks — direct
override, forged system turn, fake regulatory authority, role reassignment,
a delimiter flood and a base64-encoded instruction — appended to
unambiguously bearish articles. All six failed to move the verdict, and all
six were reported in `risk_flags`.

## Observability

`LOG_FORMAT=json` emits one object per line carrying `ts`, `level`,
`logger`, `message`, `request_id` and any `extra=` fields.

Every request gets an `X-Request-ID`. The ContextVar holding it lives in
`platform/telemetry.py` rather than in the middleware that sets it, so any
layer can stamp a line without importing the API package — which is what
makes a request traceable from the router down through a provider or model
call.

`GET /metrics` reports latency percentiles per route template, cache hit
rate, breaker states, per-provider error rates and AI token totals.
Provider error rate measures whether the provider is *working*, not whether
data exists: a 404 for a nonexistent ticker is a successful call that
returned nothing.

## Security

| | |
|---|---|
| Credentials | `SecretStr`, never logged — asserted with a sentinel key hunted through reprs, logs, tracebacks, error bodies and every metadata endpoint |
| Rate limiting | Per-client token buckets, two tiers. AI endpoints 0.2/s (burst 10) because they cost money; everything else 10/s (burst 60). `/health` exempt |
| Input | Symbol allowlist regex before anything reaches yfinance or a prompt; question length capped at the edge |
| Prompt injection | Escaped, delimited, instructions-after-data, plus the live eval above |
| CORS | Explicit origin allowlist, never `*` |
| Headers | nosniff, DENY framing, no-referrer, restrictive CSP and Permissions-Policy. HSTS left to the TLS terminator |
| Errors | One envelope; `detail` only under DEBUG, so production leaks nothing |
| Dependencies | `pip-audit` on every push, Dependabot weekly |

`X-Forwarded-For` is honoured only under `MARKETPULSE_TRUST_PROXY=1` — the
header is forgeable without a proxy in front, and a test asserts a spray of
forged addresses still hits the limit.

## Layout

```
marketpulse/
├── platform/     resilience, tiered cache, LTTB, logging, metrics
├── schema/       domain + wire models, exchange reference data
├── providers/    Protocols, typed errors, 4 providers, caching wrappers
├── services/     market orchestration, news routing
├── ai/           model registry, hardened prompts, structured analysis
├── api/          FastAPI app, DI, error envelope, middleware, v1 routers
├── client/       typed httpx client — the UI's only backend import
└── ui/           Streamlit presentation
```

## API

`GET /health` · `/health/ready` · `/info` · `/metrics`
`GET /v1/overview` · `/v1/search?q=` · `/v1/history/{symbol}` · `/v1/profile/{symbol}` · `/v1/crypto/history/{coin_id}`
`GET /v1/news/{symbol}` · `/v1/news?q=`
`GET /v1/analysis/{symbol}` · `/v1/analysis/{symbol}/stream` (SSE) · `POST /v1/insights` (SSE)

Interactive docs at `/docs`.

## Development

```bash
uv run pytest                      # 448 tests, ~12s, no network
uv run pytest --cov                # 91%, excluding the UI
uv run ruff check marketpulse
uv run mypy                        # clean
uv run lint-imports                # 3 architecture contracts
uv run pre-commit install          # the fast half of CI, before each commit

uv run python scripts/smoke_live.py      # real upstreams
uv run python scripts/smoke_ui.py        # drives the UI via AppTest
uv run python scripts/check_models.py    # probes the model catalog (needs a key)
uv run python scripts/eval_injection.py  # live injection eval (needs a key)
```

CI runs lint, formatting, mypy, the architecture contracts, tests with a
coverage gate and `pip-audit`, on 3.11 and 3.12 — plus two jobs with
specific histories behind them: one installs `requirements.txt` with plain
pip and rejects NUL bytes, and one builds the Docker image, starts it and
asserts the container is not root.

Coverage is gated at 88% over the surface pytest can exercise.
`marketpulse/ui` is excluded — Streamlit call sequences need a script
context, so a gate including them would measure the wrong thing. The UI is
covered by `scripts/smoke_ui.py` through Streamlit's own AppTest harness.

## Decisions

[docs/adr/](docs/adr/) records ten, including the ones this project does
*not* do:

- [No RAG](docs/adr/0003-no-rag.md) — five articles fit in the prompt; retrieval over a set small enough to pass whole costs latency and infrastructure for nothing
- [No LiteLLM](docs/adr/0004-no-litellm.md) — its value scales with provider count, and this has one
- [Synchronous platform layer](docs/adr/0005-synchronous-platform-layer.md) — every upstream client blocks, and wrapping blocking calls in `async` moves the blocking into the event loop
- [In-process rate limiting](docs/adr/0009-in-process-rate-limiting.md) — with a stated limit: it does not survive horizontal scaling
- [Null model pricing](docs/adr/0010-null-model-pricing.md) — a wrong cost figure is worse than an absent one

## Known limits

- **Rate limiting is per-process.** N replicas means N times the intended limit; a shared Redis bucket is needed before scaling out.
- **Per-token pricing is unset**, so cost reports as unavailable rather than estimated. Token counts are exact.
- **No persistence beyond the cache.** Sentiment history and watchlists would need a real database.
- **The Docker build is verified in CI, not locally** — Docker is not installed on the machine this was developed on. The CI job builds the image, starts it, waits for `/health` and asserts the container is not root.

## Built with

Python 3.11+ · FastAPI · Streamlit · Pydantic · httpx · pandas · Plotly ·
yfinance · CoinGecko · NewsAPI · MarketAux · Google Gemini · uv · pytest ·
ruff · mypy · import-linter · Docker
