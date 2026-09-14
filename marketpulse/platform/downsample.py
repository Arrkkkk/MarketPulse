"""Largest-Triangle-Three-Buckets downsampling.

Phase 3 reduced long series by taking every Nth row. That bounds the payload
but lies about the shape: a stride can step straight over a crash and a spike
and produce a chart that looks calm through the most important week in the
series. For price data that is not a cosmetic problem — it is the wrong
answer.

LTTB (Steinarsson, 2013, "Downsampling Time Series for Visual
Representation") keeps the number of points fixed and chooses *which* points
by area. The series is split into buckets; from each bucket it keeps the
point forming the largest triangle with the previously kept point and the
mean of the next bucket. Extremes win, because an outlier is the third
vertex of a big triangle, so peaks and troughs survive while flat stretches
collapse.

First and last points are always kept: the last one carries the current
price.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def lttb_indices(x: np.ndarray, y: np.ndarray, threshold: int) -> np.ndarray:
    """Positions of the `threshold` points that best preserve the shape."""
    n = len(x)
    if threshold >= n or threshold < 3:
        return np.arange(n)

    # threshold - 2 buckets, because the endpoints are kept unconditionally.
    every = (n - 2) / (threshold - 2)
    selected = np.empty(threshold, dtype=np.int64)
    selected[0] = 0
    a = 0  # the previously selected point, one vertex of every triangle

    for i in range(threshold - 2):
        # The averaged next bucket supplies the third vertex.
        next_start = int(np.floor((i + 1) * every) + 1)
        next_end = min(int(np.floor((i + 2) * every) + 1), n)
        if next_start >= next_end:
            next_start, next_end = n - 1, n
        avg_x = x[next_start:next_end].mean()
        avg_y = y[next_start:next_end].mean()

        start = int(np.floor(i * every) + 1)
        end = min(int(np.floor((i + 1) * every) + 1), n - 1)
        if start >= end:
            selected[i + 1] = start if start < n else n - 1
            a = int(selected[i + 1])
            continue

        # Twice the triangle area; the factor of two is constant so it does
        # not affect which point wins.
        areas = np.abs(
            (x[a] - avg_x) * (y[start:end] - y[a]) - (x[a] - x[start:end]) * (avg_y - y[a])
        )
        chosen = start + int(np.argmax(areas))
        selected[i + 1] = chosen
        a = chosen

    selected[threshold - 1] = n - 1
    return selected


def downsample_ohlcv(frame: pd.DataFrame, max_points: int) -> pd.DataFrame:
    """Reduce an OHLCV frame to at most `max_points` rows, shape preserved.

    Point selection runs on `close`, and whole rows are kept, so every bar
    returned is a real bar — open/high/low/volume stay consistent with the
    close that was selected rather than being interpolated.
    """
    n = len(frame)
    if n <= max_points or max_points < 3:
        return frame

    x = frame.index.view("int64").astype(np.float64) if isinstance(
        frame.index, pd.DatetimeIndex
    ) else np.arange(n, dtype=np.float64)
    y = frame["close"].to_numpy(dtype=np.float64)

    # A NaN close would poison the area comparison; carry the last good value
    # forward purely for the purpose of choosing points.
    if np.isnan(y).any():
        y = pd.Series(y).ffill().bfill().to_numpy()

    return frame.iloc[lttb_indices(x, y, max_points)]
