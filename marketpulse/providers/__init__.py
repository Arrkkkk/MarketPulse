"""Data providers.

Each concrete provider satisfies one of the Protocols in `protocol.py` and
obeys the contract documented there: empty means empty, failure raises.
"""

from marketpulse.providers.cached import (
    CachedCryptoProvider,
    CachedNewsProvider,
    CachedPriceProvider,
)
from marketpulse.providers.coingecko_provider import CoinGeckoProvider
from marketpulse.providers.errors import (
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    SymbolNotFound,
)
from marketpulse.providers.marketaux_provider import MarketAuxProvider
from marketpulse.providers.newsapi_provider import NewsAPIProvider
from marketpulse.providers.protocol import CryptoProvider, NewsProvider, PriceProvider
from marketpulse.providers.yfinance_provider import YFinanceProvider

__all__ = [
    "CachedCryptoProvider",
    "CachedNewsProvider",
    "CachedPriceProvider",
    "CoinGeckoProvider",
    "CryptoProvider",
    "MarketAuxProvider",
    "NewsAPIProvider",
    "NewsProvider",
    "PriceProvider",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderUnavailable",
    "RateLimited",
    "SymbolNotFound",
    "YFinanceProvider",
]
