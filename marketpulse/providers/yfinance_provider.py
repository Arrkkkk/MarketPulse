"""Yahoo Finance price provider.

Two changes from the code this replaces ([api_utils.py] `get_stock_data` and
[app.py] `get_yfinance_ticker_info`):

1. `get_histories` issues ONE batched `yf.download(..., threads=True)` instead
   of looping `yf.Ticker().history()` per symbol with a `time.sleep(0.5)`
   between each. Measured on the app's 29-symbol overview list: 45.5s -> 1.0s.

2. `.info` is no longer fetched for every symbol in the overview. It cost
   21 of those 45 seconds to populate metadata for 29 companies when at most
   one is ever on screen. It is now a separate, lazily-called method.

The yfinance callables are injected so tests can substitute fakes, and the
real module is imported lazily inside the defaults — importing this module
must not drag yfinance (and pandas' full import graph) in with it. That
pattern is from wshobson/maverick-mcp `market_data/fetchers.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from marketpulse.platform.http import RetryPolicy, get_limiter, resilient
from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.errors import (
    ProviderUnavailable,
    SymbolNotFound,
)
from marketpulse.schema.market import OHLCV_COLUMNS, CompanyProfile, PriceHistory, utcnow

logger = get_logger("providers.yfinance")

PROVIDER_NAME = "yfinance"

#: Yahoo throttles aggressively and publishes no quota. This is a
#: self-imposed ceiling, generous enough not to be felt interactively.
_RATE_PER_SECOND = 8.0


def _default_history_fn(symbol: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.Ticker(symbol).history(period=period, interval=interval)


def _default_download_fn(symbols: list[str], period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols,
        period=period,
        interval=interval,
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=True,
    )


def _default_info_fn(symbol: str) -> dict[str, Any]:
    import yfinance as yf

    return yf.Ticker(symbol).info


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Lowercase columns, keep OHLCV, sort by date.

    `auto_adjust=True` means Yahoo already folded splits and dividends into
    `close`, so there is no separate 'adj close'. The code this replaces
    checked for one and silently did nothing.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))
    out = frame.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if "adj close" in out.columns:
        out["close"] = out["adj close"]
    missing = [c for c in OHLCV_COLUMNS if c not in out.columns]
    if missing:
        return pd.DataFrame(columns=list(OHLCV_COLUMNS))
    out = out[list(OHLCV_COLUMNS)].dropna(how="all")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


class YFinanceProvider:
    """PriceProvider backed by Yahoo Finance."""

    name = PROVIDER_NAME

    def __init__(
        self,
        history_fn: Callable[..., pd.DataFrame] | None = None,
        download_fn: Callable[..., pd.DataFrame] | None = None,
        info_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._history_fn = history_fn or _default_history_fn
        self._download_fn = download_fn or _default_download_fn
        self._info_fn = info_fn or _default_info_fn
        self._limiter = get_limiter(PROVIDER_NAME, _RATE_PER_SECOND)

    # -- helpers ----------------------------------------------------------

    def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run an upstream call under rate limit, retry and breaker."""
        try:
            return resilient(
                PROVIDER_NAME,
                fn,
                *args,
                retry=RetryPolicy(attempts=3, base_delay=0.4),
                limiter=self._limiter,
                give_up_on=(SymbolNotFound,),
            )
        except (SymbolNotFound, ProviderUnavailable):
            raise
        except Exception as exc:
            # yfinance raises a wide and unstable set of exception types.
            # Anything unclassified is treated as a transient upstream fault
            # so the breaker can see it, rather than surfacing as an empty
            # result that looks like real data.
            raise ProviderUnavailable(str(exc), provider=PROVIDER_NAME) from exc

    # -- PriceProvider ----------------------------------------------------

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> PriceHistory:
        raw = self._call(self._history_fn, symbol, period, interval)
        frame = _normalise(raw)
        if frame.empty:
            # Yahoo answers unknown symbols with an empty frame rather than an
            # error, so an empty result for a single explicit symbol is the
            # only signal we get that it does not exist.
            raise SymbolNotFound(
                f"no data for {symbol!r} (period={period}, interval={interval})",
                provider=PROVIDER_NAME,
            )
        return PriceHistory(symbol=symbol, interval=interval, frame=frame, as_of=utcnow())

    def get_histories(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, PriceHistory]:
        if not symbols:
            return {}
        raw = self._call(self._download_fn, list(symbols), period, interval)
        out: dict[str, PriceHistory] = {}

        if raw is None or raw.empty:
            # Every symbol failing at once is an upstream problem, not 29
            # coincidental delistings.
            raise ProviderUnavailable(
                f"batch download returned nothing for {len(symbols)} symbols",
                provider=PROVIDER_NAME,
            )

        single = len(symbols) == 1
        for symbol in symbols:
            try:
                sub = raw if single else raw[symbol]
            except KeyError:
                continue
            frame = _normalise(sub)
            if frame.empty:
                logger.info("no data for %s in batch download; skipping", symbol)
                continue
            out[symbol] = PriceHistory(
                symbol=symbol, interval=interval, frame=frame, as_of=utcnow()
            )

        if not out:
            raise ProviderUnavailable(
                f"batch download yielded no usable data for {len(symbols)} symbols",
                provider=PROVIDER_NAME,
            )
        return out

    def get_profile(self, symbol: str) -> CompanyProfile:
        info = self._call(self._info_fn, symbol) or {}
        if not info.get("longName") and not info.get("shortName"):
            raise SymbolNotFound(f"no profile for {symbol!r}", provider=PROVIDER_NAME)
        return CompanyProfile(
            symbol=symbol,
            long_name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            summary=info.get("longBusinessSummary") or info.get("description"),
            currency=info.get("currency") or "USD",
            exchange=info.get("exchangeShortName") or info.get("exchange"),
            market_cap=info.get("marketCap"),
            fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            trailing_pe=info.get("trailingPE"),
            dividend_yield=info.get("dividendYield"),
        )
