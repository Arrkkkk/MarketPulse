"""CoinGecko provider.

Was the least-covered module in the package at 53%, despite being one of
only two price sources.
"""

from __future__ import annotations

import pytest

from marketpulse.providers.cached import CachedCryptoProvider, CachedNewsProvider
from marketpulse.providers.coingecko_provider import CoinGeckoProvider
from marketpulse.providers.errors import (
    ProviderUnavailable,
    RateLimited,
    SymbolNotFound,
)
from tests.fakes import FakeNewsProvider

PRICES = {
    "bitcoin": {
        "usd": 50_000.0,
        "usd_market_cap": 1e12,
        "usd_24h_vol": 3e10,
        "usd_24h_change": 2.5,
        "last_updated_at": 1_760_000_000,
    },
    "ethereum": {"usd": 3000.0, "usd_24h_change": -1.2, "last_updated_at": 1_760_000_000},
}


def provider(price_fn=None, chart_fn=None) -> CoinGeckoProvider:
    return CoinGeckoProvider(
        price_fn=price_fn or (lambda ids, vs: PRICES),
        chart_fn=chart_fn or (lambda c, vs, d: {"prices": [[1_760_000_000_000, 50_000.0]]}),
    )


# --- quotes ----------------------------------------------------------------


def test_quotes_are_parsed_into_typed_models():
    quotes = provider().get_quotes(["bitcoin", "ethereum"])
    assert quotes["bitcoin"].price == 50_000.0
    assert quotes["bitcoin"].change_percent_24h == 2.5
    assert quotes["bitcoin"].market_cap == 1e12
    assert quotes["ethereum"].price == 3000.0


def test_the_last_updated_timestamp_becomes_an_aware_datetime():
    quote = provider().get_quotes(["bitcoin"])["bitcoin"]
    assert quote.as_of.tzinfo is not None


def test_a_missing_timestamp_falls_back_to_now():
    quotes = provider(price_fn=lambda i, v: {"bitcoin": {"usd": 1.0}}).get_quotes(["bitcoin"])
    assert quotes["bitcoin"].as_of is not None


def test_entries_without_a_price_are_skipped():
    payload = {"bitcoin": {"usd": 1.0}, "brokencoin": {"usd_market_cap": 5.0}}
    quotes = provider(price_fn=lambda i, v: payload).get_quotes(["bitcoin", "brokencoin"])
    assert set(quotes) == {"bitcoin"}


def test_an_empty_coin_list_returns_empty_without_calling_the_upstream():
    calls = {"n": 0}

    def counting(ids, vs):
        calls["n"] += 1
        return {}

    assert provider(price_fn=counting).get_quotes([]) == {}
    assert calls["n"] == 0


def test_a_request_where_every_id_is_rejected_raises():
    """An empty mapping for a non-empty request is a caller error, not data."""
    with pytest.raises(SymbolNotFound):
        provider(price_fn=lambda i, v: {}).get_quotes(["nosuchcoin"])


def test_the_display_name_is_humanised():
    payload = {"shiba-inu": {"usd": 0.00001}}
    quote = provider(price_fn=lambda i, v: payload).get_quotes(["shiba-inu"])["shiba-inu"]
    assert quote.display_name == "Shiba Inu"


# --- history ---------------------------------------------------------------


def test_history_builds_an_ohlcv_frame():
    history = provider().get_history("bitcoin")
    assert not history.empty
    assert set(["open", "high", "low", "close", "volume"]) <= set(history.frame.columns)


def test_ohl_are_filled_from_close_rather_than_invented():
    """CoinGecko's free market-chart endpoint returns a close series only."""
    history = provider().get_history("bitcoin")
    row = history.frame.iloc[0]
    assert row["open"] == row["high"] == row["low"] == row["close"]


def test_history_has_a_datetime_index():
    import pandas as pd

    assert isinstance(provider().get_history("bitcoin").frame.index, pd.DatetimeIndex)


def test_an_unknown_coin_raises_rather_than_returning_an_empty_frame():
    with pytest.raises(SymbolNotFound):
        provider(chart_fn=lambda c, vs, d: {"prices": []}).get_history("nosuchcoin")


def test_a_null_chart_response_raises():
    with pytest.raises(SymbolNotFound):
        provider(chart_fn=lambda c, vs, d: None).get_history("bitcoin")


# --- failure classification ------------------------------------------------


def _raise(exc):
    def fn(*_a, **_k):
        raise exc

    return fn


@pytest.mark.parametrize("message", ["429 Too Many Requests", "rate limit exceeded", "Error 429"])
def test_rate_limit_messages_are_classified(message):
    with pytest.raises(RateLimited):
        provider(price_fn=_raise(Exception(message))).get_quotes(["bitcoin"])


def test_other_failures_are_unavailable_not_empty():
    with pytest.raises(ProviderUnavailable):
        provider(price_fn=_raise(ConnectionError("network down"))).get_quotes(["bitcoin"])


def test_a_chart_failure_propagates():
    with pytest.raises(ProviderUnavailable):
        provider(chart_fn=_raise(ConnectionError("down"))).get_history("bitcoin")


# --- cached crypto wrapper -------------------------------------------------


def test_cached_quotes_avoid_a_second_upstream_call(cache):
    calls = {"n": 0}

    def counting(ids, vs):
        calls["n"] += 1
        return PRICES

    wrapped = CachedCryptoProvider(provider(price_fn=counting), cache)
    wrapped.get_quotes(["bitcoin", "ethereum"])
    second = wrapped.get_quotes(["bitcoin", "ethereum"])
    assert calls["n"] == 1
    assert second["bitcoin"].cached is True


def test_quote_cache_key_ignores_id_order(cache):
    calls = {"n": 0}

    def counting(ids, vs):
        calls["n"] += 1
        return PRICES

    wrapped = CachedCryptoProvider(provider(price_fn=counting), cache)
    wrapped.get_quotes(["bitcoin", "ethereum"])
    wrapped.get_quotes(["ethereum", "bitcoin"])
    assert calls["n"] == 1, "argument order should not produce a second entry"


def test_cached_crypto_history_round_trips(cache):
    wrapped = CachedCryptoProvider(provider(), cache)
    first = wrapped.get_history("bitcoin", days="30")
    second = wrapped.get_history("bitcoin", days="30")
    assert second.cached is True
    assert len(second) == len(first)


def test_one_day_history_is_cached_more_briefly_than_a_long_window(cache):
    """A 1d series is intraday and goes stale fast; 30d is daily bars."""
    from marketpulse.platform.cache import TTL_DAILY_HISTORY, TTL_INTRADAY

    assert TTL_INTRADAY < TTL_DAILY_HISTORY
    wrapped = CachedCryptoProvider(provider(), cache)
    wrapped.get_history("bitcoin", days="1")
    wrapped.get_history("bitcoin", days="30")
    assert wrapped.get_history("bitcoin", days="1").cached is True


def test_cached_news_failures_are_not_stored(cache):
    inner = FakeNewsProvider("NewsAPI", fail_with=RateLimited("429", provider="n"))
    wrapped = CachedNewsProvider(inner, cache)
    with pytest.raises(RateLimited):
        wrapped.get_news("apple")

    inner.fail_with = None
    assert wrapped.get_news("apple").articles
