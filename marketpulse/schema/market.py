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
    currency: str = "USD"
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
    currency: str = "USD"
    exchange: str | None = None
    market_cap: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    trailing_pe: float | None = None
    dividend_yield: float | None = None
    cached: bool = False

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, v: object) -> str:
        """Yahoo reports London pence as 'GBp'; callers want the ISO code."""
        if not isinstance(v, str) or not v.strip():
            return "USD"
        return "GBP" if v == "GBp" else v


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

    def to_quote(self, currency: str = "USD") -> Quote | None:
        """Collapse the series into a quote for the most recent bar."""
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
