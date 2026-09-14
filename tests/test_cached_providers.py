"""Caching wrappers: hits, partial batches, and failure pass-through."""

from __future__ import annotations

import pytest

from marketpulse.providers.cached import CachedNewsProvider, CachedPriceProvider
from marketpulse.providers.errors import ProviderUnavailable, SymbolNotFound
from tests.fakes import FakeNewsProvider, FakePriceProvider, make_history


def test_second_call_is_served_from_cache(cache):
    inner = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    provider = CachedPriceProvider(inner, cache)

    first = provider.get_history("AAPL")
    second = provider.get_history("AAPL")

    assert len([c for c in inner.calls if c[0] == "get_history"]) == 1
    assert first.cached is False
    assert second.cached is True, "callers need to know the age of what they got"


def test_different_parameters_are_cached_separately(cache):
    inner = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    provider = CachedPriceProvider(inner, cache)
    provider.get_history("AAPL", period="1y")
    provider.get_history("AAPL", period="5d")
    assert len([c for c in inner.calls if c[0] == "get_history"]) == 2


def test_batch_fetches_only_the_symbols_that_are_missing(cache):
    """A refresh where one symbol is new should cost one batch of one."""
    inner = FakePriceProvider(histories={s: make_history(s) for s in ("AAPL", "MSFT", "NVDA")})
    provider = CachedPriceProvider(inner, cache)

    provider.get_histories(["AAPL", "MSFT"])
    inner.calls.clear()
    result = provider.get_histories(["AAPL", "MSFT", "NVDA"])

    assert set(result) == {"AAPL", "MSFT", "NVDA"}
    batched = [c for c in inner.calls if c[0] == "get_histories"]
    assert len(batched) == 1
    assert batched[0][1][0] == ("NVDA",), "only the uncached symbol should be fetched"


def test_a_fully_cached_batch_makes_no_upstream_call(cache):
    inner = FakePriceProvider(histories={s: make_history(s) for s in ("AAPL", "MSFT")})
    provider = CachedPriceProvider(inner, cache)
    provider.get_histories(["AAPL", "MSFT"])
    inner.calls.clear()

    result = provider.get_histories(["AAPL", "MSFT"])
    assert inner.calls == []
    assert all(h.cached for h in result.values())


@pytest.mark.parametrize(
    "error",
    [
        ProviderUnavailable("down", provider="f"),
        SymbolNotFound("nope", provider="f"),
    ],
)
def test_failures_propagate_and_are_not_cached(cache, error):
    inner = FakePriceProvider(fail_with=error)
    provider = CachedPriceProvider(inner, cache)

    with pytest.raises(type(error)):
        provider.get_history("AAPL")

    # The failure must not have been stored: once the upstream recovers the
    # next call has to reach it.
    inner.fail_with = None
    inner.histories = {"AAPL": make_history("AAPL")}
    assert provider.get_history("AAPL") is not None


def test_news_results_round_trip_through_the_cache(cache):
    inner = FakeNewsProvider("NewsAPI")
    provider = CachedNewsProvider(inner, cache)

    first = provider.get_news("Apple", symbol="AAPL")
    second = provider.get_news("Apple", symbol="AAPL")

    assert inner.call_count == 1
    assert second.cached is True
    assert [a.title for a in second.articles] == [a.title for a in first.articles]
    assert second.source == first.source


def test_cached_wrapper_reports_inner_configuration(cache):
    inner = FakeNewsProvider("NewsAPI", configured=False)
    assert CachedNewsProvider(inner, cache).configured is False
