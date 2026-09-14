"""Composition root.

The one place that knows which concrete provider backs which interface and
where the caching wrapper goes. Everything else takes its dependencies as
constructor arguments, so nothing else has to import a concrete provider —
and a test can assemble a service from fakes without touching this module.

Phase 3 replaces these helpers with FastAPI dependencies; the wiring they
express stays the same.
"""

from __future__ import annotations

from functools import lru_cache

from marketpulse.providers.cached import (
    CachedCryptoProvider,
    CachedNewsProvider,
    CachedPriceProvider,
)
from marketpulse.providers.coingecko_provider import CoinGeckoProvider
from marketpulse.providers.marketaux_provider import MarketAuxProvider
from marketpulse.providers.newsapi_provider import NewsAPIProvider
from marketpulse.providers.yfinance_provider import YFinanceProvider
from marketpulse.services.market_service import MarketService
from marketpulse.services.news_service import NewsService


@lru_cache(maxsize=1)
def build_analyst():
    """NewsAnalyst over a lazily-constructed Gemini client.

    Imported here rather than at module top so that the AI stack is only
    loaded when something actually asks for it.
    """
    from marketpulse.ai.analyst import NewsAnalyst

    return NewsAnalyst()


@lru_cache(maxsize=1)
def build_market_service() -> MarketService:
    """MarketService over cached yfinance and CoinGecko providers."""
    return MarketService(
        price_provider=CachedPriceProvider(YFinanceProvider()),
        crypto_provider=CachedCryptoProvider(CoinGeckoProvider()),
    )


@lru_cache(maxsize=1)
def build_news_service() -> NewsService:
    """NewsService over cached NewsAPI (US) and MarketAux (global) providers."""
    return NewsService(
        us_provider=CachedNewsProvider(NewsAPIProvider()),
        global_provider=CachedNewsProvider(MarketAuxProvider()),
    )
