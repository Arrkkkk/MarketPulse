"""CoinGecko crypto provider.

Ports `get_crypto_prices` and `get_crypto_historical_data` from api_utils.py.
Both were already batched and fast (0.38s for 11 coins), so the change here is
the contract, not the performance: a failed call now raises instead of
returning `{}`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from marketpulse.platform.http import RetryPolicy, get_limiter, resilient
from marketpulse.platform.telemetry import get_logger
from marketpulse.providers.errors import ProviderUnavailable, RateLimited, SymbolNotFound
from marketpulse.schema.market import CryptoQuote, PriceHistory, utcnow

logger = get_logger("providers.coingecko")

PROVIDER_NAME = "coingecko"

#: CoinGecko's free tier allows roughly 10-30 calls/minute and answers 429
#: sharply once exceeded. 0.4/s (24/min) sits inside that.
_RATE_PER_SECOND = 0.4


def _default_price_fn(ids: str, vs_currency: str) -> dict[str, Any]:
    from pycoingecko import CoinGeckoAPI

    return CoinGeckoAPI().get_price(
        ids=ids,
        vs_currencies=vs_currency,
        include_market_cap="true",
        include_24hr_vol="true",
        include_24hr_change="true",
        include_last_updated_at="true",
    )


def _default_chart_fn(coin_id: str, vs_currency: str, days: str) -> dict[str, Any]:
    from pycoingecko import CoinGeckoAPI

    return CoinGeckoAPI().get_coin_market_chart_by_id(
        id=coin_id, vs_currency=vs_currency, days=days
    )


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


class CoinGeckoProvider:
    """CryptoProvider backed by CoinGecko's public API."""

    name = PROVIDER_NAME

    def __init__(
        self,
        price_fn: Callable[..., dict[str, Any]] | None = None,
        chart_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._price_fn = price_fn or _default_price_fn
        self._chart_fn = chart_fn or _default_chart_fn
        self._limiter = get_limiter(PROVIDER_NAME, _RATE_PER_SECOND, burst=4)

    def _call(self, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return resilient(
                PROVIDER_NAME,
                fn,
                *args,
                retry=RetryPolicy(attempts=3, base_delay=1.0),
                limiter=self._limiter,
                give_up_on=(SymbolNotFound,),
            )
        except (SymbolNotFound, ProviderUnavailable):
            raise
        except Exception as exc:
            if _is_rate_limit(exc):
                raise RateLimited(str(exc), provider=PROVIDER_NAME) from exc
            raise ProviderUnavailable(str(exc), provider=PROVIDER_NAME) from exc

    # -- CryptoProvider ---------------------------------------------------

    def get_quotes(self, coin_ids: list[str], vs_currency: str = "usd") -> dict[str, CryptoQuote]:
        if not coin_ids:
            return {}
        raw = self._call(self._price_fn, ",".join(coin_ids), vs_currency)
        if raw is None:
            raise ProviderUnavailable("price endpoint returned nothing", provider=PROVIDER_NAME)

        out: dict[str, CryptoQuote] = {}
        for coin_id, data in raw.items():
            price = data.get(vs_currency)
            if price is None:
                continue
            ts = data.get("last_updated_at")
            out[coin_id] = CryptoQuote(
                coin_id=coin_id,
                price=float(price),
                change_percent_24h=data.get(f"{vs_currency}_24h_change"),
                market_cap=data.get(f"{vs_currency}_market_cap"),
                volume_24h=data.get(f"{vs_currency}_24h_vol"),
                vs_currency=vs_currency,
                as_of=(
                    datetime.fromtimestamp(ts, tz=UTC) if ts else utcnow()
                ),
            )
        # An empty mapping for a non-empty request means every id was
        # rejected, which is a caller error worth surfacing loudly.
        if not out:
            raise SymbolNotFound(
                f"no quotes for any of {coin_ids!r}", provider=PROVIDER_NAME
            )
        return out

    def get_history(self, coin_id: str, days: str = "30", vs_currency: str = "usd") -> PriceHistory:
        raw = self._call(self._chart_fn, coin_id, vs_currency, days)
        prices = (raw or {}).get("prices") or []
        if not prices:
            raise SymbolNotFound(
                f"no market chart for {coin_id!r} (days={days})", provider=PROVIDER_NAME
            )

        frame = pd.DataFrame(prices, columns=["timestamp", "close"])
        frame["date"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame = frame.set_index("date").drop(columns=["timestamp"])

        # CoinGecko's free market-chart endpoint returns a close series only.
        # Filling OHL from the close keeps the PriceHistory contract without
        # inventing intrabar data that was never reported.
        for col in ("open", "high", "low"):
            frame[col] = frame["close"]
        frame["volume"] = 0

        return PriceHistory(
            symbol=coin_id,
            interval="1d",
            frame=frame[["open", "high", "low", "close", "volume"]],
            as_of=utcnow(),
        )
