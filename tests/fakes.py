"""Fakes used across the test suite.

Every provider takes its upstream callable by constructor injection, so tests
substitute these and never touch the network. That is the whole reason the
injection exists.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from marketpulse.providers.errors import ProviderUnavailable, SymbolNotFound
from marketpulse.schema.market import (
    CompanyProfile,
    CryptoQuote,
    PriceHistory,
    SymbolMatch,
    utcnow,
)
from marketpulse.schema.news import NewsArticle, NewsResult


def make_frame(rows: int = 10, start_price: float = 100.0) -> pd.DataFrame:
    """A well-formed OHLCV frame with a DatetimeIndex."""
    idx = pd.date_range("2026-01-01", periods=rows, freq="D")
    closes = [start_price + i for i in range(rows)]
    return pd.DataFrame(
        {
            "open": [c - 1 for c in closes],
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1_000_000 + i for i in range(rows)],
        },
        index=idx,
    )


def make_history(symbol: str = "AAPL", rows: int = 10) -> PriceHistory:
    return PriceHistory(symbol=symbol, interval="1d", frame=make_frame(rows), as_of=utcnow())


class FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self) -> Any:
        return self._payload


class FakePriceProvider:
    """A PriceProvider that serves canned data and can be told to fail."""

    name = "fake-price"

    def __init__(
        self,
        histories: dict[str, PriceHistory] | None = None,
        profiles: dict[str, CompanyProfile] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.histories = histories or {"AAPL": make_history("AAPL")}
        self.profiles = profiles or {}
        self.fail_with = fail_with
        self.calls: list[tuple[str, tuple]] = []

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> PriceHistory:
        self.calls.append(("get_history", (symbol, period, interval)))
        self._maybe_fail()
        if symbol not in self.histories:
            raise SymbolNotFound(f"no data for {symbol}", provider=self.name)
        return self.histories[symbol]

    def get_histories(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, PriceHistory]:
        self.calls.append(("get_histories", (tuple(symbols), period, interval)))
        self._maybe_fail()
        found = {s: self.histories[s] for s in symbols if s in self.histories}
        if not found:
            raise ProviderUnavailable("nothing returned", provider=self.name)
        return found

    def get_profile(self, symbol: str) -> CompanyProfile:
        self.calls.append(("get_profile", (symbol,)))
        self._maybe_fail()
        if symbol not in self.profiles:
            raise SymbolNotFound(f"no profile for {symbol}", provider=self.name)
        return self.profiles[symbol]

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        self.calls.append(("search_symbols", (query, limit)))
        self._maybe_fail()
        return [
            SymbolMatch(symbol=s, name=f"{s} Inc.", exchange="NMS")
            for s in self.histories
            if query.lower() in s.lower()
        ][:limit]


class FakeCryptoProvider:
    name = "fake-crypto"

    def __init__(
        self,
        quotes: dict[str, CryptoQuote] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.quotes = quotes or {
            "bitcoin": CryptoQuote(coin_id="bitcoin", price=50_000.0, as_of=utcnow())
        }
        self.fail_with = fail_with

    def get_quotes(self, coin_ids: list[str], vs_currency: str = "usd") -> dict[str, CryptoQuote]:
        if self.fail_with is not None:
            raise self.fail_with
        return {c: self.quotes[c] for c in coin_ids if c in self.quotes}

    def get_history(self, coin_id: str, days: str = "30", vs_currency: str = "usd") -> PriceHistory:
        if self.fail_with is not None:
            raise self.fail_with
        return make_history(coin_id)


class FakeNewsProvider:
    """A NewsProvider whose configuration, payload and failure are all settable."""

    def __init__(
        self,
        name: str = "fake-news",
        articles: list[NewsArticle] | None = None,
        configured: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self.name = name
        self._articles = (
            articles
            if articles is not None
            else [NewsArticle(title="Headline", url="https://example.com/1", source_name=name)]
        )
        self._configured = configured
        self.fail_with = fail_with
        self.call_count = 0
        self.last_symbol: str | None = None

    @property
    def configured(self) -> bool:
        return self._configured

    def get_news(self, query: str, *, symbol: str | None = None, limit: int = 5) -> NewsResult:
        self.call_count += 1
        self.last_symbol = symbol
        if self.fail_with is not None:
            raise self.fail_with
        return NewsResult(articles=list(self._articles), source=self.name)
