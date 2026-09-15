"""Service layer: news routing and market orchestration."""

from __future__ import annotations

import pytest

from marketpulse.providers.errors import (
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
)
from marketpulse.services.market_service import MarketService
from marketpulse.services.news_service import NewsService
from tests.fakes import (
    FakeCryptoProvider,
    FakeNewsProvider,
    FakePriceProvider,
    make_history,
)

# --------------------------------------------------------------------------
# News routing — the heuristic ported from news_api_utils.get_top_news
# --------------------------------------------------------------------------


@pytest.mark.parametrize("exchange", ["NASDAQ", "NYSE", "nyse", " NYSE ARCA ", "OTC"])
def test_us_listings_prefer_newsapi(exchange):
    us, glob = FakeNewsProvider("NewsAPI"), FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Apple Inc.", exchange=exchange, symbol="AAPL")
    assert result.source == "NewsAPI"
    assert glob.call_count == 0
    assert result.fallback is False


@pytest.mark.parametrize("exchange", ["NSE", "LSE", "HKEX", None, ""])
def test_non_us_listings_and_crypto_prefer_marketaux(exchange):
    us, glob = FakeNewsProvider("NewsAPI"), FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Reliance", exchange=exchange, symbol="RELIANCE.NS")
    assert result.source == "MarketAux"
    assert us.call_count == 0


def test_marketaux_is_queried_by_symbol_not_free_text():
    """Searching a company name returns noise; searching the ticker does not."""
    glob = FakeNewsProvider("MarketAux")
    NewsService(FakeNewsProvider("NewsAPI"), glob).get_news(
        "Reliance Industries Limited", exchange="NSE", symbol="RELIANCE.NS"
    )
    assert glob.last_symbol == "RELIANCE.NS"


def test_falls_back_when_the_preferred_provider_fails():
    us = FakeNewsProvider("NewsAPI", fail_with=RateLimited("429", provider="NewsAPI"))
    glob = FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Apple", exchange="NASDAQ", symbol="AAPL")
    assert result.source == "MarketAux"
    assert result.fallback is True, "the UI needs to know this was second choice"


def test_falls_back_when_the_preferred_provider_returns_nothing():
    us = FakeNewsProvider("NewsAPI", articles=[])
    glob = FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Apple", exchange="NASDAQ", symbol="AAPL")
    assert result.source == "MarketAux"
    assert result.fallback is True


def test_fallback_works_in_the_other_direction_too():
    us = FakeNewsProvider("NewsAPI")
    glob = FakeNewsProvider("MarketAux", fail_with=ProviderUnavailable("down", provider="MA"))
    result = NewsService(us, glob).get_news("Reliance", exchange="NSE", symbol="RELIANCE.NS")
    assert result.source == "NewsAPI"
    assert result.fallback is True


def test_unconfigured_providers_are_skipped_without_being_called():
    us = FakeNewsProvider("NewsAPI", configured=False)
    glob = FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Apple", exchange="NASDAQ", symbol="AAPL")
    assert us.call_count == 0, "should not spend a call on a provider with no key"
    assert result.source == "MarketAux"


def test_raises_when_every_provider_fails():
    """The old code returned ([], 'NewsAPI.org (HTTP Error: 429)') here and the
    UI rendered 'No recent news found' — a false statement."""
    us = FakeNewsProvider("NewsAPI", fail_with=RateLimited("429", provider="NewsAPI"))
    glob = FakeNewsProvider("MarketAux", fail_with=ProviderUnavailable("500", provider="MA"))
    with pytest.raises(ProviderUnavailable):
        NewsService(us, glob).get_news("Apple", exchange="NASDAQ", symbol="AAPL")


def test_returns_empty_when_healthy_providers_genuinely_have_no_news():
    us = FakeNewsProvider("NewsAPI", articles=[])
    glob = FakeNewsProvider("MarketAux", articles=[])
    result = NewsService(us, glob).get_news("ObscureCo", exchange="NASDAQ")
    assert result.empty, "genuine emptiness is an answer, not an error"


def test_raises_when_no_provider_is_configured_at_all():
    """Returning empty here would render as "no news found for AAPL" — the
    same lie one level up. We never looked, so we must say so."""
    us = FakeNewsProvider("NewsAPI", configured=False)
    glob = FakeNewsProvider("MarketAux", configured=False)
    with pytest.raises(ProviderNotConfigured):
        NewsService(us, glob).get_news("Apple", exchange="NASDAQ")


def test_one_configured_provider_is_enough():
    us = FakeNewsProvider("NewsAPI", configured=False)
    glob = FakeNewsProvider("MarketAux")
    result = NewsService(us, glob).get_news("Apple", exchange="NASDAQ")
    assert result.source == "MarketAux"


def test_not_configured_error_is_collected_and_surfaced():
    us = FakeNewsProvider("NewsAPI", fail_with=ProviderNotConfigured("k", provider="NewsAPI"))
    glob = FakeNewsProvider("MarketAux", fail_with=ProviderNotConfigured("k", provider="MA"))
    with pytest.raises(ProviderUnavailable):
        NewsService(us, glob).get_news("Apple", exchange="NASDAQ")


# --------------------------------------------------------------------------
# Market orchestration
# --------------------------------------------------------------------------


def test_overview_fetches_stocks_and_crypto():
    prices = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    service = MarketService(
        prices, FakeCryptoProvider(), stock_symbols=("AAPL",), crypto_ids=("bitcoin",)
    )
    overview = service.get_overview()
    assert "AAPL" in overview.stocks
    assert "bitcoin" in overview.crypto
    assert not overview.degraded


def test_overview_uses_one_batched_call_not_one_per_symbol():
    """The 45.5s -> 1.4s change. A regression here is a 30x slowdown."""
    prices = FakePriceProvider(histories={s: make_history(s) for s in ("AAPL", "MSFT", "GOOGL")})
    service = MarketService(
        prices, FakeCryptoProvider(), stock_symbols=("AAPL", "MSFT", "GOOGL"), crypto_ids=()
    )
    service.get_overview()
    batched = [c for c in prices.calls if c[0] == "get_histories"]
    per_symbol = [c for c in prices.calls if c[0] == "get_history"]
    assert len(batched) == 1
    assert per_symbol == []


def test_overview_does_not_fetch_profiles_eagerly():
    """`.info` cost 21 of the original 45 seconds for data nobody was looking at."""
    prices = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    MarketService(
        prices, FakeCryptoProvider(), stock_symbols=("AAPL",), crypto_ids=()
    ).get_overview()
    assert [c for c in prices.calls if c[0] == "get_profile"] == []


def test_crypto_failure_does_not_take_down_stocks():
    prices = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    crypto = FakeCryptoProvider(fail_with=ProviderUnavailable("down", provider="cg"))
    overview = MarketService(
        prices, crypto, stock_symbols=("AAPL",), crypto_ids=("bitcoin",)
    ).get_overview()
    assert "AAPL" in overview.stocks
    assert overview.degraded
    assert "crypto" in overview.failures


def test_stock_failure_does_not_take_down_crypto():
    prices = FakePriceProvider(fail_with=ProviderUnavailable("down", provider="yf"))
    overview = MarketService(
        prices, FakeCryptoProvider(), stock_symbols=("AAPL",), crypto_ids=("bitcoin",)
    ).get_overview()
    assert "bitcoin" in overview.crypto
    assert "stocks" in overview.failures


def test_symbols_missing_from_the_batch_are_reported_not_hidden():
    prices = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    overview = MarketService(
        prices, FakeCryptoProvider(), stock_symbols=("AAPL", "DELISTED.XX"), crypto_ids=()
    ).get_overview()
    assert "DELISTED.XX" in overview.failures["missing_symbols"]


def test_missing_profile_returns_none_rather_than_raising():
    """A missing profile should grey out a panel, not fail the page."""
    prices = FakePriceProvider(histories={"AAPL": make_history("AAPL")}, profiles={})
    assert MarketService(prices, FakeCryptoProvider()).get_profile("AAPL") is None


def test_default_watchlist_has_no_delisted_tickers():
    from marketpulse.services.market_service import DEFAULT_STOCK_SYMBOLS

    assert "DAI.DE" not in DEFAULT_STOCK_SYMBOLS
    assert "TM.TO" not in DEFAULT_STOCK_SYMBOLS


# --------------------------------------------------------------------------
# Currency inference from the ticker suffix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("AAPL", "USD"),
        ("0005.HK", "HKD"),  # was labelled USD before this existed
        ("RELIANCE.NS", "INR"),
        ("TCS.NS", "INR"),
        ("SHEL.L", "GBP"),
        ("MBG.DE", "EUR"),
        ("RY.TO", "CAD"),
        ("7203.T", "JPY"),
        ("BHP.AX", "AUD"),
        ("NESN.SW", "CHF"),
    ],
)
def test_currency_is_inferred_from_the_exchange_suffix(symbol, expected):
    from marketpulse.schema.exchanges import currency_for_symbol

    assert currency_for_symbol(symbol) == expected


@pytest.mark.parametrize("symbol", ["^GSPC", "^FTSE", "FOO.ZZZ", ""])
def test_unknown_venues_return_none_rather_than_guessing_usd(symbol):
    """A wrong currency label is misinformation; an absent one is a gap."""
    from marketpulse.schema.exchanges import currency_for_symbol

    assert currency_for_symbol(symbol) is None


def test_london_pence_normalises_to_gbp():
    from marketpulse.schema.exchanges import normalise_currency

    assert normalise_currency("GBp") == "GBP"
    assert normalise_currency("") is None
