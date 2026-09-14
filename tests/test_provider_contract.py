"""The provider contract, asserted once for every implementation.

`protocol.py` documents a contract. This file is what makes it real. A new
provider is added to the parametrize list and either passes or is not done.

The central assertion is the one the old codebase got wrong everywhere:

    infrastructure failure must RAISE, never return an empty result.

Returning `[]` on a 429 is what made the UI tell users "No recent news found"
when the truth was "we were rate-limited and did not look".
"""

from __future__ import annotations

import pandas as pd
import pytest
import requests

from marketpulse.providers.coingecko_provider import CoinGeckoProvider
from marketpulse.providers.errors import (
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    SymbolNotFound,
)
from marketpulse.providers.marketaux_provider import MarketAuxProvider
from marketpulse.providers.newsapi_provider import NewsAPIProvider
from marketpulse.providers.protocol import CryptoProvider, NewsProvider, PriceProvider
from marketpulse.providers.yfinance_provider import YFinanceProvider
from tests.fakes import FakeResponse, make_frame

# --------------------------------------------------------------------------
# Structural conformance
# --------------------------------------------------------------------------


def test_yfinance_satisfies_price_provider():
    assert isinstance(YFinanceProvider(), PriceProvider)


def test_coingecko_satisfies_crypto_provider():
    assert isinstance(CoinGeckoProvider(), CryptoProvider)


@pytest.mark.parametrize("cls", [NewsAPIProvider, MarketAuxProvider])
def test_news_providers_satisfy_news_provider(cls):
    assert isinstance(cls(api_key="k"), NewsProvider)


def test_cached_wrappers_still_satisfy_their_protocols():
    """A cached provider must be substitutable for the thing it wraps."""
    from marketpulse.platform.cache import Cache
    from marketpulse.providers.cached import (
        CachedCryptoProvider,
        CachedNewsProvider,
        CachedPriceProvider,
    )

    c = Cache(db_path=":memory:")
    assert isinstance(CachedPriceProvider(YFinanceProvider(), c), PriceProvider)
    assert isinstance(CachedCryptoProvider(CoinGeckoProvider(), c), CryptoProvider)
    assert isinstance(CachedNewsProvider(NewsAPIProvider(api_key="k"), c), NewsProvider)


# --------------------------------------------------------------------------
# THE contract: failures raise
# --------------------------------------------------------------------------


def _boom(*_a, **_k):
    raise requests.ConnectionError("network is down")


def test_yfinance_raises_on_transport_failure_rather_than_returning_empty():
    provider = YFinanceProvider(history_fn=_boom, download_fn=_boom, info_fn=_boom)
    with pytest.raises(ProviderUnavailable):
        provider.get_history("AAPL")


def test_coingecko_raises_on_transport_failure():
    provider = CoinGeckoProvider(price_fn=_boom, chart_fn=_boom)
    with pytest.raises(ProviderUnavailable):
        provider.get_quotes(["bitcoin"])


@pytest.mark.parametrize("cls", [NewsAPIProvider, MarketAuxProvider])
def test_news_providers_raise_on_transport_failure(cls):
    provider = cls(get_fn=_boom, api_key="k")
    with pytest.raises(ProviderUnavailable):
        provider.get_news("Apple")


@pytest.mark.parametrize("cls", [NewsAPIProvider, MarketAuxProvider])
def test_news_providers_raise_rate_limited_on_429(cls):
    """429 is distinguishable from 'no articles' — the original bug."""
    provider = cls(get_fn=lambda *a, **k: FakeResponse(429), api_key="k")
    with pytest.raises(RateLimited):
        provider.get_news("Apple")


@pytest.mark.parametrize("cls", [NewsAPIProvider, MarketAuxProvider])
@pytest.mark.parametrize("status", [500, 502, 503])
def test_news_providers_raise_unavailable_on_5xx(cls, status):
    provider = cls(get_fn=lambda *a, **k: FakeResponse(status), api_key="k")
    with pytest.raises(ProviderUnavailable):
        provider.get_news("Apple")


@pytest.mark.parametrize("cls", [NewsAPIProvider, MarketAuxProvider])
def test_news_providers_raise_not_configured_without_a_key(cls):
    provider = cls(api_key="")
    assert provider.configured is False
    with pytest.raises(ProviderNotConfigured):
        provider.get_news("Apple")


def test_yfinance_raises_symbol_not_found_for_unknown_symbol():
    """Yahoo answers unknown symbols with an empty frame, not an error."""
    provider = YFinanceProvider(history_fn=lambda *a: pd.DataFrame())
    with pytest.raises(SymbolNotFound):
        provider.get_history("NOSUCHTICKER")


# --------------------------------------------------------------------------
# The other half: genuine emptiness is returned, not raised
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cls", "payload"),
    [(NewsAPIProvider, {"articles": []}), (MarketAuxProvider, {"data": []})],
)
def test_news_providers_return_empty_when_there_is_genuinely_no_news(cls, payload):
    """A healthy 200 with no articles is an answer, not a failure."""
    provider = cls(get_fn=lambda *a, **k: FakeResponse(200, payload), api_key="k")
    result = provider.get_news("ObscureCo")
    assert result.empty
    assert result.articles == []


def test_yfinance_returns_data_on_success():
    provider = YFinanceProvider(history_fn=lambda *a: make_frame(5))
    history = provider.get_history("AAPL")
    assert len(history) == 5
    assert not history.cached


# --------------------------------------------------------------------------
# Retry policy
# --------------------------------------------------------------------------


def test_symbol_not_found_is_not_retried():
    """A ticker that does not exist will not start existing on attempt three."""
    calls = {"n": 0}

    def counting(*_a, **_k):
        calls["n"] += 1
        return pd.DataFrame()

    provider = YFinanceProvider(history_fn=counting)
    with pytest.raises(SymbolNotFound):
        provider.get_history("NOPE")
    assert calls["n"] == 1


def test_missing_credential_is_not_retried():
    calls = {"n": 0}

    def counting(*_a, **_k):
        calls["n"] += 1
        return FakeResponse(401)

    provider = NewsAPIProvider(get_fn=counting, api_key="bad")
    with pytest.raises(ProviderNotConfigured):
        provider.get_news("Apple")
    assert calls["n"] == 1
