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


def test_a_failed_fetch_for_the_missing_symbols_does_not_lose_what_was_cached(cache):
    """The real bug this guards against: on a live deploy, a batch of 28
    cached symbols plus one new one (GOOGL) failed entirely because the
    upstream returned nothing for GOOGL alone — turning "one symbol is
    temporarily unavailable" into "the whole stocks overview is
    degraded," the exact all-or-nothing failure this project's provider
    contract exists to rule out everywhere else.
    """
    inner = FakePriceProvider(
        histories={"AAPL": make_history("AAPL"), "MSFT": make_history("MSFT")}
    )
    provider = CachedPriceProvider(inner, cache)
    provider.get_histories(["AAPL", "MSFT"])  # populate the cache

    # GOOGL isn't in `inner.histories`, so the fake raises exactly the way
    # the real yfinance provider does when a batch download comes back
    # with nothing usable for the requested symbol.
    result = provider.get_histories(["AAPL", "MSFT", "GOOGL"])

    assert set(result) == {"AAPL", "MSFT"}, "the two cached symbols must survive GOOGL's failure"


def test_a_totally_failed_batch_with_nothing_cached_still_raises(cache):
    """Empty means empty, not "we have no idea" — with nothing to fall
    back on, this must still fail loud rather than return {}."""
    inner = FakePriceProvider(histories={})
    provider = CachedPriceProvider(inner, cache)

    with pytest.raises(ProviderUnavailable):
        provider.get_histories(["GOOGL"])


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


def test_search_results_are_cached(cache):
    inner = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    provider = CachedPriceProvider(inner, cache)
    provider.search_symbols("aap")
    inner.calls.clear()
    matches = provider.search_symbols("aap")
    assert inner.calls == [], "a repeat search should not reach the upstream"
    assert matches[0].symbol == "AAPL"


def test_search_is_cached_case_insensitively(cache):
    inner = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    provider = CachedPriceProvider(inner, cache)
    provider.search_symbols("AAPL")
    inner.calls.clear()
    provider.search_symbols("  aapl ")
    assert inner.calls == []


def test_an_empty_search_result_is_also_cached(cache):
    """A nonsense query stays nonsense; re-asking costs a round trip."""
    inner = FakePriceProvider(histories={"AAPL": make_history("AAPL")})
    provider = CachedPriceProvider(inner, cache)
    assert provider.search_symbols("zzz") == []
    inner.calls.clear()
    assert provider.search_symbols("zzz") == []
    assert inner.calls == []
