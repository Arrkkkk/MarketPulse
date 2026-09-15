"""Market data endpoints.

Endpoints validate input, call one service method, and shape the result.
They contain no fetching, no caching and no error handling — providers raise,
the registered handlers in `api/errors.py` translate.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from marketpulse.api.deps import MarketServiceDep
from marketpulse.platform.downsample import downsample_ohlcv
from marketpulse.schema.api import (
    CoinId,
    HistoryResponse,
    OverviewResponse,
    ProfileResponse,
    SearchResponse,
    Symbol,
)
from marketpulse.schema.exchanges import currency_for_symbol
from marketpulse.schema.market import PriceHistory

router = APIRouter(tags=["market"])

#: Hard ceiling on bars returned in one response. IBM's full history is
#: 16,283 rows (~1.4MB of JSON) and the old UI shipped all of it to the
#: browser on every rerun.
MAX_BARS = 2000

#: What a chart actually needs. A 1200px-wide plot cannot show more than
#: about two points per pixel, so anything beyond this is bytes the user
#: pays for and cannot see.
DEFAULT_BARS = 800

#: Points in each overview tile's sparkline. get_overview() already holds a
#: year of daily bars per symbol in memory (needed for the quote itself),
#: so this costs one pandas .tail() per symbol — no new fetch, no new
#: cache entry. See docs/adr/0011-sparkline-payload.md.
SPARK_POINTS = 30


def _downsample(history: PriceHistory, max_points: int) -> tuple[PriceHistory, int]:
    """Return (possibly reduced history, original row count).

    LTTB rather than a stride: a stride can step over a crash and a spike
    and draw a calm chart through the most important week in the series.
    """
    total = len(history)
    if total <= max_points:
        return history, total
    reduced = downsample_ohlcv(history.frame, max_points)
    return history.model_copy(update={"frame": reduced}), total


@router.get("/overview", response_model=OverviewResponse, summary="Dashboard landing data")
def get_overview(service: MarketServiceDep) -> OverviewResponse:
    """Stocks and crypto for the landing view.

    Returns 200 even when one asset class failed: a dashboard that shows
    crypto while Yahoo is down is more useful than one showing nothing. The
    `failures` map and `degraded` flag tell the client what is missing so it
    can say so rather than rendering a misleading empty panel.
    """
    overview = service.get_overview()
    # Currency comes from the ticker suffix, not from a per-symbol .info
    # lookup: the batch price download carries no currency, and fetching 29
    # profiles to read one field each is what made the old cold start 45s.
    quotes = [
        q
        for symbol, h in overview.stocks.items()
        if (q := h.to_quote(currency=currency_for_symbol(symbol), spark_points=SPARK_POINTS))
        is not None
    ]
    return OverviewResponse(
        stocks=sorted(quotes, key=lambda q: q.symbol),
        crypto=sorted(overview.crypto.values(), key=lambda c: c.coin_id),
        failures=overview.failures,
        degraded=overview.degraded,
    )


@router.get("/history/{symbol}", response_model=HistoryResponse, summary="OHLCV history")
def get_history(
    symbol: Symbol,
    service: MarketServiceDep,
    period: str = Query("1y", pattern=r"^(1d|5d|1mo|3mo|6mo|1y|2y|5y|10y|ytd|max)$"),
    interval: str = Query("1d", pattern=r"^(1m|5m|15m|30m|60m|1h|1d|5d|1wk|1mo|3mo)$"),
    max_points: int = Query(DEFAULT_BARS, ge=10, le=MAX_BARS),
) -> HistoryResponse:
    history = service.get_history(symbol, period=period, interval=interval)
    reduced, total = _downsample(history, max_points)
    return HistoryResponse.from_history(reduced, total=total)


@router.get("/search", response_model=SearchResponse, summary="Find a ticker by name")
def search(
    service: MarketServiceDep,
    q: str = Query(min_length=1, max_length=64, description="Company name or ticker"),
    limit: int = Query(8, ge=1, le=20),
) -> SearchResponse:
    """Resolve free text to candidate tickers.

    Declared before /profile/{symbol} so the literal path wins over the
    parameterised one.
    """
    return SearchResponse(query=q, matches=service.search(q, limit))


@router.get("/profile/{symbol}", response_model=ProfileResponse, summary="Company profile")
def get_profile(symbol: Symbol, service: MarketServiceDep) -> ProfileResponse:
    """Descriptive metadata. Fetched lazily — never for the whole watchlist."""
    profile = service.get_profile(symbol)
    if profile is None:
        # Distinct from a provider failure: the symbol resolves, Yahoo just
        # has no profile for it. 404 is the honest answer.
        from marketpulse.providers.errors import SymbolNotFound

        raise SymbolNotFound(f"no profile for {symbol!r}", provider="yfinance")
    return ProfileResponse(**profile.model_dump())


@router.get(
    "/crypto/history/{coin_id}",
    response_model=HistoryResponse,
    summary="Cryptocurrency price history",
)
def get_crypto_history(
    coin_id: CoinId,
    service: MarketServiceDep,
    days: str = Query("30", pattern=r"^(1|7|14|30|90|180|365|max)$"),
    max_points: int = Query(DEFAULT_BARS, ge=10, le=MAX_BARS),
) -> HistoryResponse:
    history = service.get_crypto_history(coin_id, days=days)
    reduced, total = _downsample(history, max_points)
    return HistoryResponse.from_history(reduced, total=total)
