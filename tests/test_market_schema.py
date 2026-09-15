"""PriceHistory.to_quote(): the one place a NaN close cannot survive.

Found from a real Render deploy, not written speculatively: three symbols'
overview quotes failed pydantic validation on the client with `price` and
the last sparkline point both `None` — a trailing bar's NaN close (most
likely "today," fetched before the day's bar has settled) surviving all
the way from `float('nan')` on the server, through JSON's null-for-NaN
substitution on the wire, into a client-side `float` field that correctly
refuses it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketpulse.schema.market import PriceHistory, utcnow


def _shift(values: list[float], offset: float) -> list[float]:
    return [v + offset if not np.isnan(v) else v for v in values]


def _frame(closes: list[float | None], start: str = "2026-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    values = [np.nan if c is None else c for c in closes]
    return pd.DataFrame(
        {
            "open": _shift(values, -1),
            "high": _shift(values, 1),
            "low": _shift(values, -2),
            "close": values,
            "volume": [1_000_000.0] * len(values),
        },
        index=idx,
    )


def test_a_trailing_nan_close_falls_back_to_the_last_real_bar():
    history = PriceHistory(symbol="AAPL", frame=_frame([100.0, 101.0, None]), as_of=utcnow())
    quote = history.to_quote()

    assert quote is not None
    assert quote.price == 101.0, "the trailing NaN bar must not become the quote"
    assert quote.as_of.date().isoformat() == "2026-01-02", (
        "as_of must track whichever bar was actually used, not frame.index[-1]"
    )


def test_previous_close_also_skips_the_trailing_nan_bar():
    history = PriceHistory(symbol="AAPL", frame=_frame([100.0, 101.0, None]), as_of=utcnow())
    quote = history.to_quote()
    assert quote is not None
    assert quote.previous_close == 100.0


def test_a_sparkline_never_carries_the_nan_as_its_final_point():
    history = PriceHistory(symbol="AAPL", frame=_frame([100.0, 101.0, None]), as_of=utcnow())
    quote = history.to_quote(spark_points=30)

    assert quote is not None
    assert quote.spark is not None
    assert quote.spark[-1] == 101.0
    assert not any(np.isnan(v) for v in quote.spark), "no NaN anywhere in the sparkline"


def test_a_frame_with_no_real_close_at_all_yields_no_quote():
    """Empty means empty — the honest failure this project's whole
    provider contract exists to preserve, not a Quote with nothing real
    in it."""
    history = PriceHistory(symbol="AAPL", frame=_frame([None, None]), as_of=utcnow())
    assert history.to_quote() is None


def test_a_nan_open_high_or_low_becomes_none_not_a_validation_error():
    """Same class of bug as the close, guarded the same way volume
    already was — open/high/low are optional on Quote for exactly this
    reason."""
    frame = _frame([100.0])
    frame.loc[frame.index[-1], "high"] = np.nan
    history = PriceHistory(symbol="AAPL", frame=frame, as_of=utcnow())

    quote = history.to_quote()

    assert quote is not None
    assert quote.price == 100.0
    assert quote.high is None
