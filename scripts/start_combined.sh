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

echo "start_combined: launching API on 127.0.0.1:${API_PORT}"
uvicorn marketpulse.api.main:app --host 127.0.0.1 --port "${API_PORT}" &
API_PID=$!

# Wait for the API to actually accept connections before starting
# Streamlit, so the first page load doesn't race a uvicorn process that
# hasn't bound its socket yet. No curl/nc dependency — python3 is already
# in the image.
#
# Fails loud rather than starting the UI anyway on a timeout: the same
# rule ADR 1 established for the providers applies here too — a UI that
# comes up "successfully" in front of an API that never started would be
# the identical dishonesty, just one layer higher. Exiting non-zero here
# makes the platform show a failed deploy, which is what actually
# happened, instead of a UI that loads fine and then lies on every page.
#
# 90s, not 15: Render's free instance is 0.1 CPU / 512MB. This process
# cold-imports pandas, yfinance and plotly before uvicorn's socket can
# even become connectable — comfortably under a second on a real machine,
# an unverified guess on hardware that thin. Generous here costs nothing
# on a healthy start (the loop still breaks the moment the socket answers)
# and only matters on a genuinely slow one.
ready=0
for i in $(seq 1 180); do
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
    ready=1
    echo "start_combined: API accepting connections after ${i} check(s)"
    break
  fi
  sleep 0.5
done

if [[ "$ready" -ne 1 ]]; then
  echo "start_combined: API never accepted a connection on 127.0.0.1:${API_PORT} after 90s — failing rather than starting a UI with nothing behind it" >&2
  kill "$API_PID" 2>/dev/null || true
  exit 1
fi

echo "start_combined: launching UI on 0.0.0.0:${UI_PORT}"
streamlit run app.py \
  --server.port="${UI_PORT}" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false &
UI_PID=$!

# Supervise both: if either process exits — a crash, not just "this
# script exiting" — bring the whole container down so the platform
# restarts it, rather than leaving the survivor running against a dead
# partner. Not `trap ... EXIT` on an `exec`'d process: exec replaces this
# shell's own process image without ever running its EXIT traps, which
# would make that trap dead code the moment the UI actually started —
# both processes are backgrounded instead, specifically so this loop can
# keep watching both.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$UI_PID" 2>/dev/null; do
  sleep 2
done

echo "start_combined: one process exited — stopping the other and exiting" >&2
kill "$API_PID" "$UI_PID" 2>/dev/null || true
wait 2>/dev/null || true
exit 1
