# Deploying

Two processes from one image: the API and the Streamlit UI that consumes it.

## Locally

```bash
docker compose up --build
```

UI on <http://localhost:8501>, API docs on <http://localhost:8000/docs>.
Compose reads `GEMINI_API_KEY`, `NEWS_API_KEY` and `MARKETAUX_API_KEY` from
your environment or a `.env` file, and holds the SQLite cache in a named
volume so a rebuild does not send the first visitor back to the upstreams.

Without Docker:

```bash
uv sync
uv run uvicorn marketpulse.api.main:app --reload    # :8000
uv run streamlit run app.py                         # :8501
```

## Google Cloud Run

`scripts/deploy_cloud_run.sh` is committed and configured — the Cloud Run
equivalent of `fly.toml`, since Cloud Run has no single declarative file
for "two services, one private, one public, from one image."

One-time setup:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com
```

Then, from the repo root:

```bash
GEMINI_API_KEY=... NEWS_API_KEY=... MARKETAUX_API_KEY=... \
  ./scripts/deploy_cloud_run.sh
```

Every credential is optional, same rule as everywhere else in this project
— an unset one is skipped rather than deployed empty, and the script says
so. It builds straight from the committed `Dockerfile` via Cloud Build
(`--source .`; no registry to configure by hand), deploys the API and UI
as separate Cloud Run services from that one image, and prints the UI's
public URL when done.

**How the API stays private, on a platform with no private-network
primitive the way Fly has one.** The API service deploys with
`--no-allow-unauthenticated`: Cloud Run's own front end rejects any caller
without a valid Google-signed ID token before the request reaches FastAPI
at all. The UI is the one caller that's supposed to succeed, so it attaches
that token itself — see `marketpulse/client/client.py`'s
`_cloud_run_id_token()`, which activates only for an `https://` base URL
and fails silently (no auth header, same as every other optional feature
here) anywhere that isn't actually Cloud Run. The script's last step grants
the UI's service account the `roles/run.invoker` permission the token needs
to actually be honoured — Cloud Run does not infer trust between two
services from them sharing a project or a service account.

`MARKETPULSE_TRUST_PROXY=1` is set for the same reason as on Fly: Cloud
Run's front end also terminates TLS and sets `X-Forwarded-For`, so the rate
limiter can trust it. Same caveat — never set this anywhere the service is
reachable by a route that bypasses that front end.

**Cache is per-instance, not per-deployment.** Cloud Run gives each
container instance a writable filesystem, so the SQLite cache tier works
exactly as written with no code change — but it lives only as long as that
instance does. A new instance (redeploy, scale-from-zero, or Cloud Run
recycling one after a period of no traffic) starts cold, unlike Fly's
mounted volume, which survives all three. Traffic light enough to scale to
zero between visits will therefore pay the cold-fetch cost more often here
than on Fly.

## Fly.io

`fly.toml` is committed and configured.

```bash
fly launch --no-deploy --copy-config
fly secrets set GEMINI_API_KEY=... NEWS_API_KEY=... MARKETAUX_API_KEY=...
fly volumes create marketpulse_cache --size 1 --region iad
fly deploy
```

The UI is public; the API has no `[http_service]` block, so it is reachable
only over Fly's private network. The paid AI endpoints are therefore not
exposed to the internet at all — the rate limiter is a second line of
defence rather than the only one.

`MARKETPULSE_TRUST_PROXY=1` is set because Fly terminates TLS and supplies
`X-Forwarded-For`. Without it the rate limiter would see every request as
coming from the proxy and throttle all users as a single client. Do **not**
set it anywhere the service is reachable directly: the header is forgeable,
and a caller could mint a fresh identity per request.

## Container image

Published to `ghcr.io/arrkkkk/marketpulse` by `.github/workflows/publish.yml`,
which runs only after CI passes on `main` or on a `v*` tag. An image that
failed its own tests never reaches the registry. Built for amd64 and arm64
with a provenance attestation.

```bash
docker run -p 8000:8000 -e GEMINI_API_KEY=... ghcr.io/arrkkkk/marketpulse:latest
```

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | — | Absent: AI analysis reports as unconfigured |
| `NEWS_API_KEY` | — | Absent: US news falls back to MarketAux |
| `MARKETAUX_API_KEY` | — | Absent: non-US news falls back to NewsAPI |
| `MARKETPULSE_API_URL` | `http://localhost:8000` | Where the UI finds the API |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `text` | `json` for one object per line |
| `MARKETPULSE_RATELIMIT` | on | `0` disables |
| `MARKETPULSE_TRUST_PROXY` | off | `1` honours `X-Forwarded-For` |
| `MARKETPULSE_PREWARM` | on | `0` skips the startup cache warm |

Every credential is optional and each one gates exactly one feature. With
none set, the price dashboard works fully and the rest reports itself as
unconfigured rather than failing quietly.

## Operating

| Endpoint | Purpose |
|---|---|
| `/health` | Liveness. Touches no dependency, so an upstream outage cannot get the container restarted |
| `/health/ready` | Readiness. Breaker states plus a cache round trip; reports `degraded` rather than failing, since a warm cache is still useful while an upstream is down |
| `/metrics` | Latency percentiles per route, cache hit rate, breaker states, provider error rates, AI token totals |

Set `LOG_FORMAT=json` in anything that ingests logs. Every line carries a
`request_id`, and the same id is returned as `X-Request-ID`, so a user can
quote it and you can find the exact request across every layer that touched
it.

### When something is wrong

- **`/v1/analysis` returns 503 `ai_not_configured`** — no `GEMINI_API_KEY`.
- **429 from the AI endpoints** — working as intended; they are limited to
  0.2/s (burst 10) because each call costs money. Raise it in
  `api/middlewares/ratelimit.py` if the bill allows.
- **`provider_circuit_open` in a response** — that upstream failed five
  times running and requests to it are paused for 30s. It recovers on its
  own; `/metrics` shows the breaker state.
- **Slow first request after a deploy** — the pre-warm thread has not
  finished. It takes about 1.7s and only runs once.
- **Rate limiting seems ineffective behind a proxy** — `MARKETPULSE_TRUST_PROXY`
  is not set, so every request looks like it comes from the proxy address.

### Known limits

- **Rate limiting is in-process.** N replicas means N times the intended
  limit. A shared Redis bucket is needed before scaling horizontally
  ([ADR 9](adr/0009-in-process-rate-limiting.md)).
- **The cache's SQLite tier is per-machine.** Two API machines keep two
  caches; correct, just less efficient than a shared one.
- **Model pricing is unset**, so cost is reported as unavailable rather than
  estimated ([ADR 10](adr/0010-null-model-pricing.md)).
