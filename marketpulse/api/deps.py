"""FastAPI dependencies.

The composition root for the HTTP layer. Endpoints declare what they need and
never construct it, so a test can override one dependency and drive the whole
app against fakes:

    app.dependency_overrides[get_market_service] = lambda: FakeMarketService()

The services themselves are process-wide singletons (they are stateless
apart from the shared cache), so building them per request would throw away
the cache's memory tier on every call.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from marketpulse.ai.analyst import NewsAnalyst
from marketpulse.config import Settings, get_settings
from marketpulse.services.factory import (
    build_analyst,
    build_market_service,
    build_news_service,
)
from marketpulse.services.market_service import MarketService
from marketpulse.services.news_service import NewsService


def get_market_service() -> MarketService:
    return build_market_service()


def get_news_service() -> NewsService:
    return build_news_service()


def get_analyst() -> NewsAnalyst:
    return build_analyst()


def get_config() -> Settings:
    return get_settings()


AnalystDep = Annotated[NewsAnalyst, Depends(get_analyst)]
MarketServiceDep = Annotated[MarketService, Depends(get_market_service)]
NewsServiceDep = Annotated[NewsService, Depends(get_news_service)]
SettingsDep = Annotated[Settings, Depends(get_config)]
