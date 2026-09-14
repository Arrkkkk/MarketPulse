"""LTTB downsampling and latency metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketpulse.platform.downsample import downsample_ohlcv, lttb_indices
from marketpulse.platform.metrics import Metrics


def frame_with_closes(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        },
        index=idx,
    )


# --- size and endpoints ----------------------------------------------------


@pytest.mark.parametrize("target", [3, 10, 100, 799])
def test_output_never_exceeds_the_target(target):
    frame = frame_with_closes(list(np.sin(np.linspace(0, 40, 5000)) * 100 + 500))
    assert len(downsample_ohlcv(frame, target)) <= target


def test_a_short_series_is_returned_unchanged():
    frame = frame_with_closes([1.0, 2.0, 3.0])
    out = downsample_ohlcv(frame, 800)
    assert out is frame


def test_first_and_last_bars_are_always_kept():
    """The last bar carries the current price."""
    frame = frame_with_closes(list(range(1000)))
    out = downsample_ohlcv(frame, 50)
    assert out.index[0] == frame.index[0]
    assert out.index[-1] == frame.index[-1]
    assert out.iloc[-1]["close"] == frame.iloc[-1]["close"]


def test_output_stays_in_chronological_order():
    frame = frame_with_closes(list(np.random.default_rng(0).normal(100, 5, 2000)))
    out = downsample_ohlcv(frame, 100)
    assert out.index.is_monotonic_increasing


def test_rows_are_kept_whole_not_interpolated():
    """Every returned bar must be a real bar: open/high/low/volume have to
    stay consistent with the close that was selected."""
    frame = frame_with_closes(list(np.random.default_rng(1).normal(100, 5, 1000)))
    out = downsample_ohlcv(frame, 50)
    for ts, row in out.iterrows():
        original = frame.loc[ts]
        assert row["close"] == original["close"]
        assert row["high"] == original["high"]


# --- the property that justifies LTTB over a stride ------------------------


def test_a_spike_survives_downsampling_where_a_stride_would_lose_it():
    closes = [100.0] * 1000
    closes[497] = 500.0   # a spike a stride of 20 steps straight over
    closes[503] = 10.0    # and a crash
    frame = frame_with_closes(closes)

    lttb = downsample_ohlcv(frame, 50)
    stride = frame.iloc[:: (len(frame) // 50) + 1]

    assert 500.0 in set(lttb["close"]), "LTTB dropped the spike"
    assert 10.0 in set(lttb["close"]), "LTTB dropped the crash"
    assert 500.0 not in set(stride["close"]), "the stride was expected to miss it"


def test_the_extremes_of_a_real_looking_series_are_preserved():
    rng = np.random.default_rng(7)
    closes = list(np.cumsum(rng.normal(0, 1, 4000)) + 500)
    frame = frame_with_closes(closes)
    out = downsample_ohlcv(frame, 200)

    # Within a fraction of a percent of the true range, rather than exactly
    # equal: LTTB optimises area, and is not contractually a min/max filter.
    full_range = frame["close"].max() - frame["close"].min()
    kept_range = out["close"].max() - out["close"].min()
    assert kept_range / full_range > 0.97


def test_nan_closes_do_not_break_selection():
    closes = [100.0] * 500
    closes[100] = float("nan")
    frame = frame_with_closes(closes)
    assert len(downsample_ohlcv(frame, 50)) <= 50


def test_lttb_indices_are_unique_and_in_range():
    x = np.arange(1000, dtype=float)
    y = np.sin(x / 50) * 100
    idx = lttb_indices(x, y, 100)
    assert len(idx) == 100
    assert len(set(idx.tolist())) == 100
    assert idx.min() == 0 and idx.max() == 999


# --- metrics ---------------------------------------------------------------


def test_percentiles_are_computed_over_observations():
    m = Metrics()
    for value in range(1, 101):
        m.observe("GET /x", float(value))
    stats = m.snapshot()["latency"]["GET /x"]
    assert stats["count"] == 100
    assert stats["p50_ms"] == pytest.approx(50, abs=2)
    assert stats["p95_ms"] == pytest.approx(95, abs=2)
    assert stats["max_ms"] == 100


def test_the_sample_window_is_bounded():
    m = Metrics(window=10)
    for value in range(1000):
        m.observe("GET /x", float(value))
    assert m.snapshot()["latency"]["GET /x"]["count"] == 10


def test_percentiles_expose_a_slow_tail_that_a_mean_would_hide():
    """The reason for tracking p95 rather than an average."""
    m = Metrics()
    for _ in range(95):
        m.observe("GET /x", 5.0)
    for _ in range(5):
        m.observe("GET /x", 3000.0)
    stats = m.snapshot()["latency"]["GET /x"]
    mean = (95 * 5 + 5 * 3000) / 100
    assert stats["p50_ms"] == 5.0
    assert stats["p95_ms"] >= 3000.0
    assert mean < 200, "the mean looks fine while one call in twenty takes 3s"


def test_counters_accumulate():
    m = Metrics()
    m.increment("status_2xx")
    m.increment("status_2xx", 4)
    assert m.snapshot()["counters"]["status_2xx"] == 5


def test_the_timer_records_elapsed_time():
    m = Metrics()
    with m.timer("work"):
        pass
    assert m.snapshot()["latency"]["work"]["count"] == 1
