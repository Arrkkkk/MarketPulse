"""MarketAux news provider.

Ports `_fetch_news_from_marketaux` from news_api_utils.py, including the part
worth keeping: when an exchange-qualified ticker is known, MarketAux is
queried by `symbols` rather than free text. Searching "Reliance Industries
Limited" returns noise; searching `RELIANCE.NS` returns that company.
"""

from __future__ import annotations

from collections.abc import Callable
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

logger = get_logger("providers.marketaux")

PROVIDER_NAME = "MarketAux"
_ENDPOINT = "https://api.marketaux.com/v1/news/all"

_RATE_PER_SECOND = 0.5
_TIMEOUT = 10.0


class MarketAuxProvider:
    """NewsProvider backed by MarketAux. Better coverage outside the US."""

    name = PROVIDER_NAME

    def __init__(
        self,
        get_fn: Callable[..., requests.Response] | None = None,
        api_key: str | None = None,
    ) -> None:
        self._get = get_fn or requests.get
        self._api_key = api_key if api_key is not None else secret(get_settings().MARKETAUX_API_KEY)
        self._limiter = get_limiter("marketaux", _RATE_PER_SECOND, burst=3)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def get_news(self, query: str, *, symbol: str | None = None, limit: int = 5) -> NewsResult:
        if not self.configured:
            raise ProviderNotConfigured("MARKETAUX_API_KEY is not set", provider=PROVIDER_NAME)

        params: dict[str, Any] = {
            "api_token": self._api_key,
            "limit": limit,
            "sort": "published_at",
            "direction": "desc",
        }
        # Prefer the precise symbol lookup; fall back to free text for crypto
        # and anything without a ticker.
        if symbol:
            params["symbols"] = symbol.upper()
        else:
            params["search"] = query

        def _fetch() -> list[dict[str, Any]]:
            resp = self._get(_ENDPOINT, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                raise RateLimited("quota exhausted", provider=PROVIDER_NAME)
            if resp.status_code in (401, 402, 403):
                raise ProviderNotConfigured(
                    f"rejected credential (HTTP {resp.status_code})", provider=PROVIDER_NAME
                )
            if resp.status_code >= 400:
                raise ProviderUnavailable(f"HTTP {resp.status_code}", provider=PROVIDER_NAME)
            return (resp.json() or {}).get("data") or []

        try:
            raw = resilient(
                "marketaux",
                _fetch,
                retry=RetryPolicy(attempts=3, base_delay=1.0),
                limiter=self._limiter,
                give_up_on=(ProviderNotConfigured,),
            )
        except (ProviderNotConfigured, ProviderUnavailable):
            raise
        except requests.RequestException as exc:
            raise ProviderUnavailable(str(exc), provider=PROVIDER_NAME) from exc

        articles = []
        for a in raw:
            if not a.get("url"):
                continue
            source = a.get("source")
            if isinstance(source, dict):
                source = source.get("name")
            articles.append(
                NewsArticle(
                    title=a.get("title") or "Untitled",
                    url=a["url"],
                    description=a.get("description"),
                    source_name=source or PROVIDER_NAME,
                    published_at=a.get("published_at"),
                )
            )
        logger.info("fetched %d articles (symbol=%s query=%r)", len(articles), symbol, query)
        return NewsResult(articles=articles, source=PROVIDER_NAME)
