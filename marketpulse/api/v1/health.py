"""Liveness, readiness, and service metadata.

`/health` and `/health/ready` are deliberately different questions. Liveness
is "should this container be restarted"; readiness is "should it receive
traffic". Conflating them means a transient upstream outage gets your process
killed.
"""

from __future__ import annotations

from fastapi import APIRouter

from marketpulse.api.deps import SettingsDep
from marketpulse.platform.cache import get_cache
from marketpulse.platform.http import get_breaker
from marketpulse.schema.api import HealthResponse, ReadinessResponse, ServiceInfo
from marketpulse.services.market_service import DEFAULT_CRYPTO_IDS, DEFAULT_STOCK_SYMBOLS

VERSION = "0.3.0"

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health() -> HealthResponse:
    """Is the process up. Never touches a dependency, never fails on one."""
    return HealthResponse(status="ok", version=VERSION)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness")
def readiness() -> ReadinessResponse:
    """Can this instance serve useful traffic.

    Reports each provider's breaker state and whether the cache responds.
    Degraded rather than down: with a warm cache the service is still useful
    while an upstream is failing, so an open breaker is reported, not fatal.
    """
    checks: dict[str, str] = {}
    for name in ("yfinance", "coingecko", "newsapi", "marketaux"):
        checks[f"breaker:{name}"] = get_breaker(name).state

    try:
        cache = get_cache()
        cache.set("__readiness__", {"ok": True}, ttl=5)
        checks["cache"] = "ok" if cache.get("__readiness__") else "degraded"
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = f"error: {type(exc).__name__}"

    unhealthy = checks["cache"] != "ok"
    return ReadinessResponse(status="degraded" if unhealthy else "ready", checks=checks)


@router.get("/info", response_model=ServiceInfo, summary="Service capabilities")
def info(settings: SettingsDep) -> ServiceInfo:
    """What this service can do, for a client to adapt to on connect.

    Lets the UI hide the AI panel when no Gemini key is configured, instead
    of offering a button that returns an error string.
    """
    return ServiceInfo(
        name="marketpulse",
        version=VERSION,
        default_stock_symbols=list(DEFAULT_STOCK_SYMBOLS),
        default_crypto_ids=list(DEFAULT_CRYPTO_IDS),
        news_enabled=settings.news_enabled,
        ai_enabled=settings.gemini_enabled,
    )


@router.get("/metrics", summary="Cache and breaker counters")
def metrics() -> dict:
    """Plain-JSON counters.

    Not Prometheus yet — Phase 9 adds the exporter and latency histograms.
    Cache hit rate is here now because it is the number that tells you
    whether the Phase 2 caching is actually working in production.
    """
    return {
        "cache": get_cache().stats(),
        "breakers": {
            name: get_breaker(name).state
            for name in ("yfinance", "coingecko", "newsapi", "marketaux")
        },
    }
