# Multi-stage: dependencies resolve once and are cached independently of the
# source, so a code change does not re-download the world.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency layer: invalidated only by a manifest change.
# README.md is here because pyproject declares `readme = "README.md"`, so
# hatchling reads it while building the project — without it the second
# `uv sync` fails with "Readme file does not exist".
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Project layer.
COPY marketpulse ./marketpulse
COPY scripts ./scripts
COPY app.py ./
# Theme config, self-hosted fonts and the one stylesheet — see
# .streamlit/config.toml. Streamlit resolves the static/ folder relative to
# app.py's directory, so it has to sit next to it, in the image as in the
# repo.
COPY .streamlit ./.streamlit
COPY static ./static
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.14-slim AS runtime

# Run unprivileged. Nothing here needs root.
RUN useradd --create-home --uid 10001 marketpulse

# Create the cache directory *in the image*, owned by the runtime user.
# Docker seeds a named volume from the image path it is mounted over,
# including ownership — without this the volume would arrive owned by root
# and the unprivileged process could not write its SQLite cache tier.
RUN mkdir -p /home/marketpulse/.marketpulse \
    && chown -R marketpulse:marketpulse /home/marketpulse

WORKDIR /app
COPY --from=builder --chown=marketpulse:marketpulse /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/marketpulse

USER marketpulse
EXPOSE 8000 8501

# Overridden by compose for the UI service.
CMD ["uvicorn", "marketpulse.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
