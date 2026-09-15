#!/usr/bin/env bash
# Deploy MarketPulse to Google Cloud Run: two services from one image,
# matching compose.yaml and fly.toml's shape — this is the Cloud Run
# equivalent of the config those two files commit for their platforms.
#
# One-time setup (see docs/deploying.md for the full walkthrough):
#   gcloud auth login
#   gcloud config set project YOUR_PROJECT_ID
#   gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
#     secretmanager.googleapis.com cloudbuild.googleapis.com
#
# Then, from the repo root:
#   GEMINI_API_KEY=... NEWS_API_KEY=... MARKETAUX_API_KEY=... \
#     ./scripts/deploy_cloud_run.sh
#
# Every credential is optional, same rule as everywhere else in this
# project: an unset one is skipped rather than deployed empty, and the
# matching feature reports itself as unconfigured instead of failing.
#
# Cloud Run has no notion of "private network" the way Fly does — the API
# service is instead deployed with --no-allow-unauthenticated, so Cloud
# Run's own front end rejects any request without a valid ID token before
# it ever reaches FastAPI. The UI authenticates its own calls to the API
# for exactly this reason (see marketpulse/client/client.py:_cloud_run_id_token);
# nothing else does, and nothing else needs to.

# No -u: SECRET_ARGS below is legitimately empty when no key is supplied,
# and "${arr[@]}" on an empty array is a hard "unbound variable" error
# under `set -u` on bash < 4.4 — which is exactly what ships as /bin/bash
# on stock macOS (3.2, frozen there for licensing reasons since 2007).
set -eo pipefail

REGION="${REGION:-us-central1}"
API_SERVICE="${API_SERVICE:-marketpulse-api}"
UI_SERVICE="${UI_SERVICE:-marketpulse-ui}"

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
if [[ -z "$PROJECT_ID" ]]; then
  echo "No project set. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

# --- secrets ---------------------------------------------------------------
# Created or updated only for keys actually supplied in the environment.
# Each secret name matches the env var the app reads, so --set-secrets
# below can map them 1:1.
declare -a SECRET_ARGS=()
for key in GEMINI_API_KEY NEWS_API_KEY MARKETAUX_API_KEY; do
  value="${!key:-}"
  if [[ -n "$value" ]]; then
    if gcloud secrets describe "$key" >/dev/null 2>&1; then
      printf '%s' "$value" | gcloud secrets versions add "$key" --data-file=-
    else
      printf '%s' "$value" | gcloud secrets create "$key" --data-file=-
    fi
    SECRET_ARGS+=("--set-secrets" "${key}=${key}:latest")
  else
    echo "skipping ${key} (not set) — the matching feature reports itself as unconfigured"
  fi
done

# --- API service -------------------------------------------------------------
# --no-allow-unauthenticated: reachable at the network level, but Cloud
# Run's front end rejects every caller without a valid ID token before the
# request reaches this process. --source . builds straight from the
# committed Dockerfile via Cloud Build — no registry to set up by hand.
echo "==> deploying ${API_SERVICE}"
gcloud run deploy "$API_SERVICE" \
  --source . \
  --region "$REGION" \
  --port 8000 \
  --no-allow-unauthenticated \
  --set-env-vars "LOG_FORMAT=json,LOG_LEVEL=INFO,MARKETPULSE_TRUST_PROXY=1" \
  --memory 1Gi \
  --min-instances 0 \
  "${SECRET_ARGS[@]}"

API_URL="$(gcloud run services describe "$API_SERVICE" --region "$REGION" --format='value(status.url)')"
echo "==> ${API_SERVICE} at ${API_URL} (not directly callable — see below)"

# --- UI service --------------------------------------------------------------
# Same image, different command: the Dockerfile's default CMD runs
# uvicorn, so the UI service overrides it to run Streamlit instead.
echo "==> deploying ${UI_SERVICE}"
gcloud run deploy "$UI_SERVICE" \
  --source . \
  --region "$REGION" \
  --port 8501 \
  --command streamlit \
  --args "run,app.py,--server.port=8501,--server.address=0.0.0.0,--server.headless=true,--browser.gatherUsageStats=false" \
  --allow-unauthenticated \
  --set-env-vars "LOG_FORMAT=json,LOG_LEVEL=INFO,MARKETPULSE_TRUST_PROXY=1,MARKETPULSE_API_URL=${API_URL}" \
  --memory 512Mi \
  --min-instances 0

# --- authorize the UI to call the API ---------------------------------------
# Cloud Run does not treat two services sharing a service account as
# implicitly trusting each other — the invoker role has to be granted
# explicitly, same as any other IAM binding.
UI_SERVICE_ACCOUNT="$(gcloud run services describe "$UI_SERVICE" --region "$REGION" \
  --format='value(spec.template.spec.serviceAccountName)')"
if [[ -z "$UI_SERVICE_ACCOUNT" ]]; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  UI_SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi

echo "==> granting ${UI_SERVICE_ACCOUNT} run.invoker on ${API_SERVICE}"
gcloud run services add-iam-policy-binding "$API_SERVICE" \
  --region "$REGION" \
  --member "serviceAccount:${UI_SERVICE_ACCOUNT}" \
  --role "roles/run.invoker"

UI_URL="$(gcloud run services describe "$UI_SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "Done. Dashboard: ${UI_URL}"
