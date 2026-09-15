#!/usr/bin/env bash
# Single-container entrypoint: both processes, one free web service.
#
# Exists for exactly one reason: Render's free tier has no private-service
# option at all, and even a *free web* service "can't receive private
# network traffic" (confirmed against Render's own free-tier docs) — so
# the two-service design in render.yaml's paid form (API as a private
# service, reached over Render's private network) cannot run for free.
#
# This runs both processes inside one container instead. The API binds to
# 127.0.0.1 only — not merely "not publicly routed" the way a private
# service is, genuinely unreachable from anywhere but this same container,
# since it never touches a network interface a platform-level check or a
# neighbour service could ever reach. Streamlit binds to 0.0.0.0 on
# whatever port the platform expects and is the only thing actually
# exposed.
#
# Same rule as everywhere else in this project: every credential is
# optional, and this script requires none of its own.

set -euo pipefail

API_PORT="${API_PORT:-8000}"
UI_PORT="${PORT:-8501}"  # Render sets $PORT (10000 by default); falls back
                          # to 8501 for docker run / other platforms.

export MARKETPULSE_API_URL="http://127.0.0.1:${API_PORT}"

uvicorn marketpulse.api.main:app --host 127.0.0.1 --port "${API_PORT}" &
API_PID=$!

# If the API process dies, bring the whole container down rather than
# serve a UI that can never reach it — Render then restarts the container,
# which is the correct response to that failure, not a UI stuck showing
# "cannot reach the MarketPulse API" forever.
trap 'kill "$API_PID" 2>/dev/null' EXIT

# Wait for the API to actually accept connections before starting
# Streamlit, so the first page load doesn't race a uvicorn process that
# hasn't bound its socket yet. No curl/nc dependency — python3 is already
# in the image.
for _ in $(seq 1 30); do
  if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('127.0.0.1', ${API_PORT}))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

exec streamlit run app.py \
  --server.port="${UI_PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false
