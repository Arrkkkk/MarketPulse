"""Wire models — the contract between the service and its clients.

These are what cross HTTP, so they are the one place where a `PriceHistory`
stops being a DataFrame and becomes something JSON can carry. Both the
FastAPI app and the typed client import this module, so the contract cannot
drift between the two halves.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

import pandas as pd
from pydantic import BaseModel, Field, StringConstraints

from marketpulse.schema.market import CompanyProfile, CryptoQuote, PriceHistory, Quote
from marketpulse.schema.news import NewsArticle

#: Exchange-qualified tickers: AAPL, RELIANCE.NS, 0005.HK, BRK-B, and index
#: symbols like ^GSPC / ^FTSE, which are the reason for the optional leading
#: caret (Yahoo prefixes every index that way).
#:
#: Anything outside this never reaches yfinance or an LLM prompt. The old UI
#: passed raw `st.text_input` straight through to both.
SYMBOL_PATTERN = r"^\^?[A-Za-z0-9][A-Za-z0-9.\-=]{0,19}$"
SYMBOL_RE = re.compile(SYMBOL_PATTERN)

Symbol = Annotated[
    str,
    StringConstraints(pattern=SYMBOL_PATTERN, strip_whitespace=True, to_upper=True),
]

#: CoinGecko ids are lowercase slugs: bitcoin, shiba-inu, binancecoin.
COIN_ID_PATTERN = r"^[a-z0-9][a-z0-9\-]{0,39}$"
CoinId = Annotated[
    str,
    StringConstraints(pattern=COIN_ID_PATTERN, strip_whitespace=True, to_lower=True),
]


def is_valid_symbol(value: str) -> bool:
    return bool(SYMBOL_RE.match(value.strip()))


# --- errors ---------------------------------------------------------------


class ErrorBody(BaseModel):
    """The single error shape every endpoint emits.

    `detail` is populated only when the service runs with DEBUG logging, so
    production responses never leak internals to a client.
    """

    error: str = Field(description="Stable machine-readable code, e.g. 'symbol_not_found'")
    message: str = Field(description="Human-readable explanation, safe to display")
    detail: Any | None = None
    request_id: str | None = None


# --- market ---------------------------------------------------------------


class OHLCVBar(BaseModel):
    t: datetime
    o: float
    h: float
    low: float = Field(alias="l")
    c: float
    v: float

    model_config = {"populate_by_name": True}


class HistoryResponse(BaseModel):
    symbol: str
    interval: str
    as_of: datetime
    cached: bool = False
    #: Rows actually returned, after any downsampling.
    count: int
    #: Rows the provider held before downsampling; a client can tell it is
    #: looking at a reduced series rather than the whole history.
    total: int
    bars: list[OHLCVBar]

    @classmethod
    def from_history(cls, history: PriceHistory, total: int | None = None) -> HistoryResponse:
        frame = history.frame
        bars = [
            OHLCVBar(
                t=idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx,
                o=float(row["open"]),
                h=float(row["high"]),
                l=float(row["low"]),  # noqa: E741 — matches the wire field name
                c=float(row["close"]),
                v=float(row["volume"]) if pd.notna(row["volume"]) else 0.0,
            )
            for idx, row in frame.iterrows()
        ]
        return cls(
            symbol=history.symbol,
            interval=history.interval,
            as_of=history.as_of,
            cached=history.cached,
            count=len(bars),
            total=total if total is not None else len(bars),
            bars=bars,
        )


class OverviewResponse(BaseModel):
    """The dashboard landing payload.

    Quotes, not full histories: the overview renders one number and one delta
    per symbol, so shipping a year of bars for 29 tickers would be waste.

    `failures` is part of the response rather than a log line. It is how the
    UI can say "crypto is unavailable" instead of silently rendering an empty
    panel — the specific dishonesty this whole refactor is aimed at.
    """

    stocks: list[Quote]
    crypto: list[CryptoQuote]
    failures: dict[str, str] = Field(default_factory=dict)
    degraded: bool = False


class ProfileResponse(CompanyProfile):
    pass


# --- news -----------------------------------------------------------------


class NewsResponse(BaseModel):
    articles: list[NewsArticle]
    source: str
    fallback: bool = False
    cached: bool = False


# --- service metadata -----------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    """Dependency-level readiness, as opposed to "the process is alive"."""

    status: str
    checks: dict[str, str]


class ServiceInfo(BaseModel):
    """What the client fetches on connect, so it can adapt to the service."""

    name: str
    version: str
    default_stock_symbols: list[str]
    default_crypto_ids: list[str]
    news_enabled: bool
    ai_enabled: bool
