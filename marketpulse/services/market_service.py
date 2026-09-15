"""Market data orchestration.

Sits between the providers and whatever is rendering. Knows which symbols the
overview shows and how to assemble a quote; knows nothing about Streamlit,
HTTP, or which upstream answered.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.errors import ProviderError, SymbolNotFound
from marketpulse.providers.protocol import CryptoProvider, PriceProvider
from marketpulse.schema import CompanyProfile, CryptoQuote, PriceHistory, Quote
from marketpulse.schema.exchanges import currency_for_symbol
from marketpulse.schema.market import SymbolMatch

logger = get_logger("services.market")

#: The overview watchlist. Data, not code — it used to be a literal buried in
#: the middle of a Streamlit callback, which is how two delisted tickers
#: (DAI.DE, TM.TO) survived in it.
DEFAULT_STOCK_SYMBOLS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "TSLA",
    "NVDA",
    "SPY",
    "NFLX",
    "META",
    "RELIANCE.NS",
    "0005.HK",
    "IBM",
    "JPM",
    "XOM",
    "GS",
    "BAC",
    "KO",
    "PEP",
    "DIS",
    "NKE",
    "V",
    "PG",
    "COST",
    "TCS.NS",
    "HDFCBANK.NS",
    "BARC.L",
    "SHEL.L",
    "MBG.DE",
    "RY.TO",
)

DEFAULT_CRYPTO_IDS: tuple[str, ...] = (
    "bitcoin",
    "ethereum",
    "ripple",
    "cardano",
    "solana",
    "dogecoin",
    "litecoin",
    "polkadot",
    "binancecoin",
    "tron",
    "shiba-inu",
)


@dataclass
class Overview:
    """The dashboard's landing payload.

    `failures` is first-class. The old code called `st.warning` from inside a
    cached function for each missing symbol; carrying the failures out as data
    lets the caller decide how to present them, and lets a test assert on them.
    """

    stocks: dict[str, PriceHistory] = field(default_factory=dict)
    crypto: dict[str, CryptoQuote] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return bool(self.failures)


class MarketService:
    def __init__(
        self,
        price_provider: PriceProvider,
        crypto_provider: CryptoProvider,
        stock_symbols: tuple[str, ...] = DEFAULT_STOCK_SYMBOLS,
        crypto_ids: tuple[str, ...] = DEFAULT_CRYPTO_IDS,
    ) -> None:
        self._prices = price_provider
        self._crypto = crypto_provider
        self.stock_symbols = stock_symbols
        self.crypto_ids = crypto_ids

    def get_overview(self, period: str = "1y", interval: str = "1d") -> Overview:
        """Stocks and crypto for the landing view, fetched concurrently.

        The two upstreams are independent, so they run in parallel; each is
        allowed to fail without taking the other down. A dashboard that shows
        crypto while Yahoo is down beats one that shows nothing.
        """
        overview = Overview()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="overview") as pool:
            stocks_future = pool.submit(
                self._prices.get_histories, list(self.stock_symbols), period, interval
            )
            crypto_future = pool.submit(self._crypto.get_quotes, list(self.crypto_ids))

            try:
                overview.stocks = stocks_future.result()
            except ProviderError as exc:
                logger.warning("stock overview failed: %s", exc)
                overview.failures["stocks"] = str(exc)

            try:
                overview.crypto = crypto_future.result()
            except ProviderError as exc:
                logger.warning("crypto overview failed: %s", exc)
                overview.failures["crypto"] = str(exc)

        # Symbols the batch silently dropped (delisted, or no bars in range).
        missing = [s for s in self.stock_symbols if s not in overview.stocks]
        if missing and "stocks" not in overview.failures:
            overview.failures["missing_symbols"] = ", ".join(missing)

        return overview

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> PriceHistory:
        return self._prices.get_history(symbol, period, interval)

    def get_profile(self, symbol: str) -> CompanyProfile | None:
        """Company metadata, or None when the provider has none.

        Profile data is decoration: a missing one should grey out a panel, not
        fail the page. Genuine infrastructure errors still propagate.
        """
        try:
            return self._prices.get_profile(symbol)
        except SymbolNotFound:
            return None

    def get_quote(self, symbol: str, period: str = "5d") -> Quote | None:
        history = self._prices.get_history(symbol, period=period, interval="1d")
        profile = self.get_profile(symbol)
        currency = (profile.currency if profile else None) or currency_for_symbol(symbol)
        return history.to_quote(currency=currency)

    def search(self, query: str, limit: int = 8) -> list[SymbolMatch]:
        return self._prices.search_symbols(query, limit)

    def get_crypto_history(self, coin_id: str, days: str = "30") -> PriceHistory:
        return self._crypto.get_history(coin_id, days)
