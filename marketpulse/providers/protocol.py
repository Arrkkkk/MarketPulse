"""The interfaces every data source implements.

Structural typing via `Protocol`: a class satisfies one of these by having the
right methods, with no base class to inherit and no registry to update. That
keeps providers, their cached wrappers, and the fakes used in tests
interchangeable without any of them knowing about each other.

THE CONTRACT — this is the part that matters
---------------------------------------------
An empty result means the data genuinely does not exist. Infrastructure
failure must RAISE.

A provider that returns `[]` or `{}` when the network is down, a key is
missing, or a quota is exhausted destroys the caller's ability to tell
"nothing to show" from "we failed to look". The old MarketPulse did exactly
that in all six of its fetch functions, and the UI reported "No recent news
found" whenever NewsAPI rate-limited us.

So:
  - no data for a valid input        -> return empty (`[]`, or an empty frame)
  - symbol does not exist            -> raise SymbolNotFound
  - credential missing               -> raise ProviderNotConfigured
  - quota exhausted                  -> raise RateLimited
  - network / 5xx / timeout          -> raise ProviderUnavailable

Adapted from virattt/ai-hedge-fund `hedge_fund/data/protocol.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from marketpulse.schema import CompanyProfile, CryptoQuote, NewsResult, PriceHistory
from marketpulse.schema.market import SymbolMatch


@runtime_checkable
class PriceProvider(Protocol):
    """A source of equity or index price data."""

    #: Stable identifier used for cache keys, breaker names and logging.
    name: str

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> PriceHistory:
        """OHLCV bars for one symbol.

        Returns a PriceHistory whose frame may be empty when the symbol is
        valid but has no bars in range. Raises SymbolNotFound when the symbol
        itself is unknown.
        """
        ...

    def get_histories(
        self, symbols: list[str], period: str = "1y", interval: str = "1d"
    ) -> dict[str, PriceHistory]:
        """OHLCV bars for many symbols, batched where the upstream allows it.

        Symbols that return no data are omitted from the mapping rather than
        mapped to an empty history, so callers can report which ones failed.
        A total upstream failure raises; partial failure does not.
        """
        ...

    def get_profile(self, symbol: str) -> CompanyProfile:
        """Descriptive metadata for one symbol."""
        ...

    def search_symbols(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        """Resolve a free-text company name to candidate tickers.

        An empty list means the query matched nothing — which is a real
        answer for a nonsense query and must not be confused with a search
        service that failed.
        """
        ...


@runtime_checkable
class CryptoProvider(Protocol):
    """A source of cryptocurrency market data."""

    name: str

    def get_quotes(self, coin_ids: list[str], vs_currency: str = "usd") -> dict[str, CryptoQuote]:
        """Current quotes, keyed by coin id."""
        ...

    def get_history(self, coin_id: str, days: str = "30", vs_currency: str = "usd") -> PriceHistory:
        """Historical prices. Only `close` is populated; CoinGecko's free
        market-chart endpoint does not return true OHLC, so open/high/low are
        filled from the close rather than invented."""
        ...


@runtime_checkable
class NewsProvider(Protocol):
    """A source of financial news."""

    name: str

    #: True when the credential this provider needs is present. Lets the
    #: routing service skip a provider it knows cannot answer, instead of
    #: burning a call to find out.
    @property
    def configured(self) -> bool: ...

    def get_news(
        self,
        query: str,
        *,
        symbol: str | None = None,
        limit: int = 5,
    ) -> NewsResult:
        """Recent articles for a company or topic.

        `symbol` is the exchange-qualified ticker when one is known; providers
        that can search by symbol should prefer it over the free-text query,
        which is far noisier.
        """
        ...
