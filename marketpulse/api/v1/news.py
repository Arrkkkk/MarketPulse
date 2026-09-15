"""News endpoints.

Two entry points because there are two shapes of request. A listed equity has
a ticker and an exchange, which is what drives the routing heuristic. A
cryptocurrency or a general topic has neither and can only be searched by
text.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from marketpulse.api.deps import MarketServiceDep, NewsServiceDep
from marketpulse.schema.api import NewsResponse, Symbol

router = APIRouter(tags=["news"])


@router.get("/news/{symbol}", response_model=NewsResponse, summary="News for a listed company")
def get_news_for_symbol(
    symbol: Symbol,
    news: NewsServiceDep,
    market: MarketServiceDep,
    limit: int = Query(5, ge=1, le=25),
) -> NewsResponse:
    """News for one ticker.

    The company's profile supplies two things the routing needs: the exchange
    (which decides NewsAPI vs MarketAux) and the long name (a far better
    free-text query than the bare ticker). A missing profile degrades to
    searching by symbol rather than failing.
    """
    profile = market.get_profile(symbol)
    query = profile.long_name if profile and profile.long_name else symbol
    exchange = profile.exchange if profile else None

    result = news.get_news(query, exchange=exchange, symbol=symbol, limit=limit)
    return NewsResponse(**result.model_dump())


@router.get("/news", response_model=NewsResponse, summary="News for a free-text query")
def search_news(
    news: NewsServiceDep,
    q: str = Query(min_length=2, max_length=120, description="Topic or asset name"),
    limit: int = Query(5, ge=1, le=25),
) -> NewsResponse:
    """Topic search, used for crypto and general market queries.

    No exchange, so the routing sends this to MarketAux first with NewsAPI as
    the fallback — the same order the original code used for crypto.
    """
    result = news.get_news(q, exchange=None, symbol=None, limit=limit)
    return NewsResponse(**result.model_dump())
