"""News routing.

This is the one genuinely good piece of product logic in the original
codebase, ported from `get_top_news` in news_api_utils.py and kept intact:

    US-listed equity        -> NewsAPI first, MarketAux as fallback
    everything else         -> MarketAux by ticker first, NewsAPI as fallback

NewsAPI has the better US coverage; MarketAux can be queried by an
exchange-qualified symbol, which is far more precise for non-US listings than
searching a company's name.

What changed is the failure handling. Before, a provider that failed returned
`([], "NewsAPI.org (HTTP Error: 429)")` and the UI printed that string as the
news source while telling the user no news existed. Now providers raise, this
service decides whether a fallback is warranted, and an exhausted set of
providers raises rather than fabricating an empty result.
"""

from __future__ import annotations

from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.errors import (
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
)
from marketpulse.providers.protocol import NewsProvider
from marketpulse.schema.news import NewsResult

logger = get_logger("services.news")

#: yfinance `exchangeShortName` values that mean "US-listed".
US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "NYSE ARCA", "NYSEAMERICAN", "AMEX", "OTC"})


class NewsService:
    """Routes a news request to whichever provider is likeliest to answer."""

    def __init__(self, us_provider: NewsProvider, global_provider: NewsProvider) -> None:
        self._us = us_provider
        self._global = global_provider

    @staticmethod
    def is_us_listed(exchange: str | None) -> bool:
        return bool(exchange) and exchange.strip().upper() in US_EXCHANGES

    def get_news(
        self,
        query: str,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 5,
    ) -> NewsResult:
        """Fetch news, preferring the provider best suited to the listing.

        Falls back to the other provider when the preferred one fails or
        returns nothing. Raises only when every configured provider failed —
        an empty result from a healthy provider is a real answer and is
        returned as one.
        """
        if self.is_us_listed(exchange):
            primary, secondary = self._us, self._global
        else:
            primary, secondary = self._global, self._us

        errors: list[ProviderError] = []
        attempted = False

        for provider, is_fallback in ((primary, False), (secondary, True)):
            if not provider.configured:
                logger.info("skipping %s: not configured", provider.name)
                continue
            attempted = True
            try:
                result = provider.get_news(query, symbol=symbol, limit=limit)
            except ProviderNotConfigured as exc:
                errors.append(exc)
                continue
            except ProviderUnavailable as exc:
                logger.warning("%s unavailable: %s", provider.name, exc)
                errors.append(exc)
                continue

            if result.empty and not is_fallback:
                # A healthy provider with nothing to say. Worth asking the
                # other one before concluding there is no news.
                logger.info("%s returned no articles; trying fallback", provider.name)
                continue

            return result.model_copy(update={"fallback": is_fallback})

        if errors:
            # Every provider that could have answered, failed. Say so rather
            # than returning an empty list the UI would render as "no news".
            raise ProviderUnavailable(
                "; ".join(str(e) for e in errors), provider="news"
            )

        if not attempted:
            # No key for either provider, so we never looked. Returning an
            # empty list here would render as "no news found for AAPL" —
            # the same lie, one level up, that this service exists to stop.
            raise ProviderNotConfigured(
                "no news provider is configured (set NEWS_API_KEY and/or "
                "MARKETAUX_API_KEY)",
                provider="news",
            )

        # Providers were healthy and genuinely had nothing.
        return NewsResult(articles=[], source=primary.name)
