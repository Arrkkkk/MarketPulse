"""NewsAPI.org news provider.

Ports `_fetch_news_from_newsapi` from news_api_utils.py. Behaviour preserved:
last 7 days, relevancy sort, English. What changed is that an HTTP error now
raises a typed exception instead of returning `([], "NewsAPI.org (HTTP Error: 429)")`
— a tuple whose error text the UI rendered as if it were a source name.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import requests

from marketpulse.config import get_settings, secret
from marketpulse.platform.http import RetryPolicy, get_limiter, resilient
from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.errors import (
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
)
from marketpulse.schema.news import NewsArticle, NewsResult

logger = get_logger("providers.newsapi")

PROVIDER_NAME = "NewsAPI.org"
_ENDPOINT = "https://newsapi.org/v2/everything"

#: Free tier is 100 requests/day. Throttle hard; the cache absorbs the rest.
_RATE_PER_SECOND = 0.5
_TIMEOUT = 10.0


class NewsAPIProvider:
    """NewsProvider backed by NewsAPI.org. Best coverage for US equities."""

    name = PROVIDER_NAME

    def __init__(
        self,
        get_fn: Callable[..., requests.Response] | None = None,
        api_key: str | None = None,
    ) -> None:
        self._get = get_fn or requests.get
        self._api_key = api_key if api_key is not None else secret(get_settings().NEWS_API_KEY)
        self._limiter = get_limiter("newsapi", _RATE_PER_SECOND, burst=3)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def get_news(self, query: str, *, symbol: str | None = None, limit: int = 5) -> NewsResult:
        if not self.configured:
            raise ProviderNotConfigured("NEWS_API_KEY is not set", provider=PROVIDER_NAME)

        params: dict[str, Any] = {
            "q": query,
            "language": "en",
            "sortBy": "relevancy",
            "from": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            "pageSize": limit,
            "apiKey": self._api_key,
        }

        def _fetch() -> list[dict[str, Any]]:
            resp = self._get(_ENDPOINT, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                raise RateLimited("quota exhausted", provider=PROVIDER_NAME)
            if resp.status_code in (401, 403):
                # Not retryable: the key is wrong, not the network.
                raise ProviderNotConfigured(
                    f"rejected credential (HTTP {resp.status_code})", provider=PROVIDER_NAME
                )
            if resp.status_code >= 400:
                raise ProviderUnavailable(f"HTTP {resp.status_code}", provider=PROVIDER_NAME)
            return (resp.json() or {}).get("articles") or []

        try:
            raw = resilient(
                "newsapi",
                _fetch,
                retry=RetryPolicy(attempts=3, base_delay=1.0),
                limiter=self._limiter,
                give_up_on=(ProviderNotConfigured,),
            )
        except (ProviderNotConfigured, ProviderUnavailable):
            raise
        except requests.RequestException as exc:
            raise ProviderUnavailable(str(exc), provider=PROVIDER_NAME) from exc

        articles = [
            NewsArticle(
                title=a.get("title") or "Untitled",
                url=a.get("url") or "",
                description=a.get("description"),
                source_name=(a.get("source") or {}).get("name"),
                published_at=a.get("publishedAt"),
            )
            for a in raw
            if a.get("url")
        ]
        logger.info("fetched %d articles for %r", len(articles), query)
        # An empty list here is a real answer: the query matched nothing.
        return NewsResult(articles=articles, source=PROVIDER_NAME)
