"""Market domain models.

Scalar and metadata values are Pydantic models — they are small, they cross
the API boundary, and validation is cheap. Time series stay as a pandas
DataFrame inside a typed `PriceHistory` wrapper: converting 16,000 OHLCV bars
into Pydantic objects to hand them straight to Plotly would cost real time and
buy nothing. The wrapper still carries the metadata (symbol, interval, as_of)
that the bare DataFrame used to leave implicit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from marketpulse.schema.exchanges import normalise_currency

#: Columns every PriceHistory frame is guaranteed to have, lowercase.
OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Quote(BaseModel):
    """A point-in-time equity quote derived from the latest available bar."""

    symbol: str
    price: float
    previous_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    #: None means "we do not know", not "USD". A wrong currency label is
    #: misinformation; an absent one is a small gap.
    currency: str | None = None
    as_of: datetime
    cached: bool = False

    @property
    def change(self) -> float | None:
        if self.previous_close is None:
            return None
        return self.price - self.previous_close

    @property
    def change_percent(self) -> float | None:
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100


class CryptoQuote(BaseModel):
    """A cryptocurrency quote as CoinGecko reports it."""

    coin_id: str
    price: float
    change_percent_24h: float | None = None
    market_cap: float | None = None
    volume_24h: float | None = None
    as_of: datetime
    vs_currency: str = "usd"
    cached: bool = False

    @property
    def display_name(self) -> str:
        return self.coin_id.replace("-", " ").title()


class CompanyProfile(BaseModel):
    """Descriptive metadata for a listed company.

    Expensive to fetch (`yfinance` `.info` is ~0.7s per symbol), so it is
    fetched lazily for the symbol actually on screen rather than eagerly for
    every symbol in the overview.
    """

    symbol: str
    long_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    summary: str | None = None
    currency: str | None = None
    exchange: str | None = None
    market_cap: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    trailing_pe: float | None = None
    dividend_yield: float | None = None
    cached: bool = False

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, v: object) -> str | None:
        """Yahoo reports minor units ('GBp' for London pence) as their own
        code; callers want ISO 4217."""
        return normalise_currency(v) if isinstance(v, str) else None


class SymbolMatch(BaseModel):
    """One result from a symbol search.

    Exists because the old UI required users to already know that Reliance
    is `RELIANCE.NS` and HSBC Hong Kong is `0005.HK`. It printed a 60-row
    table of exchange suffixes and left the rest to them.
    """

    symbol: str
    name: str | None = None
    exchange: str | None = None
    quote_type: str | None = None
    currency: str | None = None

    @property
    def label(self) -> str:
        parts = [self.symbol]
        if self.name:
            parts.append(f"— {self.name}")
        if self.exchange:
            parts.append(f"({self.exchange})")
        return " ".join(parts)


class PriceHistory(BaseModel):
    """An OHLCV series plus the metadata describing what it is."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    interval: str = "1d"
    frame: pd.DataFrame = Field(repr=False)
    as_of: datetime = Field(default_factory=utcnow)
    cached: bool = False

    @field_validator("frame")
    @classmethod
    def _check_columns(cls, frame: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in OHLCV_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"PriceHistory frame is missing columns: {missing}")
        return frame

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def empty(self) -> bool:
        return self.frame.empty

    @property
    def latest(self) -> pd.Series | None:
        return None if self.frame.empty else self.frame.iloc[-1]

    @property
    def previous_close(self) -> float | None:
        if len(self.frame) < 2:
            return None
        return float(self.frame.iloc[-2]["close"])

    def to_quote(self, currency: str | None = None) -> Quote | None:
        """Collapse the series into a quote for the most recent bar.

        `currency` is passed in because an OHLCV frame does not carry one.
        Callers that know it (from a profile, or from the ticker suffix via
        `currency_for_symbol`) should supply it; the rest get None.
        """
        last = self.latest
        if last is None:
            return None
        index_ts = self.frame.index[-1]
        as_of = index_ts.to_pydatetime() if hasattr(index_ts, "to_pydatetime") else utcnow()
        return Quote(
            symbol=self.symbol,
            price=float(last["close"]),
            previous_close=self.previous_close,
            open=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            volume=int(last["volume"]) if pd.notna(last["volume"]) else None,
            currency=currency,
            as_of=as_of,
            cached=self.cached,
        )

    def tail_days(self, days: int) -> PriceHistory:
        """A copy narrowed to the most recent `days` rows.

        The UI used to request `period='max'` and render every bar — 16,283
        rows (~1.4MB of JSON) for IBM. Windowing happens here so both the API
        and the UI get it for free.
        """
        return self.model_copy(update={"frame": self.frame.tail(days)})
