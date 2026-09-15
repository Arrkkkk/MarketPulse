"""Caching wrappers.

Caching is a decorator over the Protocol rather than something each provider
implements. Every provider gets it for free, it is tested once, and a provider
stays a thing that only knows how to fetch.

    provider = CachedPriceProvider(YFinanceProvider())
    provider.get_history("AAPL")   # upstream
    provider.get_history("AAPL")   # cache, ~0ms

Failure semantics are inherited, which is the important part: only successful
responses are written, and every exception propagates untouched. Caching a
failure would turn a thirty-second outage into a twelve-hour one.

Adapted from virattt/ai-hedge-fund `hedge_fund/data/cached.py`.
"""

from __future__ import annotations

from marketpulse.platform.cache import (
    TTL_DAILY_HISTORY,
    TTL_INTRADAY,
    TTL_NEWS,
    TTL_PROFILE,
    TTL_QUOTE,
    Cache,
    get_cache,
    make_key,
)
from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.protocol import CryptoProvider, NewsProvider, PriceProvider
from marketpulse.schema import CompanyProfile, CryptoQuote, NewsResult, PriceHistory
from marketpulse.schema.market import SymbolMatch

logger = get_logger("providers.cached")


def _history_ttl(interval: str) -> int:
    """Daily bars change once a day; intraday bars change constantly."""
    return TTL_DAILY_HISTORY if interval in ("1d", "5d", "1wk", "1mo", "3mo") else TTL_INTRADAY


class CachedPriceProvider:
    """PriceProvider that memoizes another PriceProvider."""

    def __init__(self, inner: PriceProvider, cache: Cache | None = None) -> None:
        self._inner = inner
        self._cache = cache or get_cache()
        self.name = inner.name

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> PriceHistory:
        key = make_key(
            f"{self.name}:history", symbol=symbol, period=period, interval=interval
        )
        frame = self._cache.get(key)
        if frame is not None:
            return PriceHistory(symbol=symbol, interval=interval, frame=frame, cached=True)
        result = self._inner.get_history(symbol, period, interval)
        self._cache.set(key, result.frame, _history_ttl(interval))
        return result

    def get_histories(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, PriceHistory]:
        """Serve what is cached, fetch only the rest.

        Partial hits matter here: the overview list changes rarely, so after
        the first load a refresh usually needs zero upstream calls, and a
        newly added symbol costs one batch of one.
        """
        ttl = _history_ttl(interval)
        out: dict[str, PriceHistory] = {}
        missing: list[str] = []

        for symbol in symbols:
            key = make_key(
                f"{self.name}:history", symbol=symbol, period=period, interval=interval
            )
            frame = self._cache.get(key)
            if frame is not None:
                out[symbol] = PriceHistory(
                    symbol=symbol, interval=interval, frame=frame, cached=True
                )
            else:
                missing.append(symbol)

        if missing:
            logger.info("batch: %d cached, %d to fetch", len(out), len(missing))
            fetched = self._inner.get_histories(missing, period, interval)
            for symbol, history in fetched.items():
                key = make_key(
                    f"{self.name}:history", symbol=symbol, period=period, interval=interval
                )
                self._cache.set(key, history.frame, ttl)
                out[symbol] = history

        return out

    def get_profile(self, symbol: str) -> CompanyProfile:
        key = make_key(f"{self.name}:profile", symbol=symbol)
        payload = self._cache.get(key)
        if payload is not None:
            return CompanyProfile.model_validate({**payload, "cached": True})
        profile = self._inner.get_profile(symbol)
        self._cache.set(key, profile.model_dump(mode="json"), TTL_PROFILE)
        return profile

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        key = make_key(f"{self.name}:search", q=query.lower().strip(), limit=limit)
        payload = self._cache.get(key)
        if payload is not None:
            return [SymbolMatch.model_validate(m) for m in payload]
        matches = self._inner.search_symbols(query, limit)
        # Cached for a day: the mapping from a company name to its ticker is
        # about as stable as data gets. An empty result is cached too — a
        # nonsense query stays nonsense, and re-asking costs a round trip.
        self._cache.set(key, [m.model_dump(mode="json") for m in matches], TTL_PROFILE)
        return matches


class CachedCryptoProvider:
    """CryptoProvider that memoizes another CryptoProvider."""

    def __init__(self, inner: CryptoProvider, cache: Cache | None = None) -> None:
        self._inner = inner
        self._cache = cache or get_cache()
        self.name = inner.name

    def get_quotes(self, coin_ids: list[str], vs_currency: str = "usd") -> dict[str, CryptoQuote]:
        # Quotes are cached as one batch: CoinGecko prices every id in a
        # single call, so per-id entries would buy nothing and cost a request
        # per cache miss.
        key = make_key(
            f"{self.name}:quotes", ids=",".join(sorted(coin_ids)), vs=vs_currency
        )
        payload = self._cache.get(key)
        if payload is not None:
            return {
                cid: CryptoQuote.model_validate({**q, "cached": True})
                for cid, q in payload.items()
            }
        quotes = self._inner.get_quotes(coin_ids, vs_currency)
        self._cache.set(
            key, {cid: q.model_dump(mode="json") for cid, q in quotes.items()}, TTL_QUOTE
        )
        return quotes

    def get_history(self, coin_id: str, days: str = "30", vs_currency: str = "usd") -> PriceHistory:
        key = make_key(f"{self.name}:history", coin=coin_id, days=days, vs=vs_currency)
        frame = self._cache.get(key)
        if frame is not None:
            return PriceHistory(symbol=coin_id, frame=frame, cached=True)
        result = self._inner.get_history(coin_id, days, vs_currency)
        # "1" day is an intraday series and goes stale quickly; longer windows
        # are effectively daily bars.
        self._cache.set(key, result.frame, TTL_INTRADAY if days == "1" else TTL_DAILY_HISTORY)
        return result


class CachedNewsProvider:
    """NewsProvider that memoizes another NewsProvider."""

    def __init__(self, inner: NewsProvider, cache: Cache | None = None) -> None:
        self._inner = inner
        self._cache = cache or get_cache()
        self.name = inner.name

    @property
    def configured(self) -> bool:
        return self._inner.configured

    def get_news(self, query: str, *, symbol: str | None = None, limit: int = 5) -> NewsResult:
        key = make_key(f"{self.name}:news", q=query, symbol=symbol or "", limit=limit)
        payload = self._cache.get(key)
        if payload is not None:
            return NewsResult.model_validate({**payload, "cached": True})
        result = self._inner.get_news(query, symbol=symbol, limit=limit)
        self._cache.set(key, result.model_dump(mode="json"), TTL_NEWS)
        return result
