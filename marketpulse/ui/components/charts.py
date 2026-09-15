"""Chart rendering."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from marketpulse.client import bars_to_frame
from marketpulse.schema.api import HistoryResponse
from marketpulse.ui import theme
from marketpulse.ui.components.state import ICON_EMPTY

#: Ranges offered in the selector. The old UI always requested `period='max'`
#: and rendered every bar — 16,283 rows for IBM, ~1.4MB of JSON per rerun.
RANGE_OPTIONS: dict[str, str] = {
    "1M": "1mo",
    "6M": "6mo",
    "YTD": "ytd",
    "1Y": "1y",
    "5Y": "5y",
    "Max": "max",
}

CRYPTO_RANGE_OPTIONS: dict[str, str] = {
    "1D": "1",
    "1W": "7",
    "1M": "30",
    "3M": "90",
    "1Y": "365",
    "Max": "max",
}


def range_selector(key: str, options: dict[str, str], default: str = "1Y") -> str:
    """A horizontal range picker. Returns the provider-level period value."""
    labels = list(options)
    chosen = st.radio(
        "Range",
        labels,
        index=labels.index(default) if default in labels else 0,
        horizontal=True,
        key=key,
        label_visibility="collapsed",
    )
    return options[chosen]


def _data_table(response: HistoryResponse, currency: str) -> None:
    """A text alternative to the chart.

    A Plotly canvas is opaque to a screen reader, and "look at the chart" is
    not an answer for someone who cannot. The same numbers, as a table,
    behind a disclosure so it costs sighted users nothing.
    """
    frame = bars_to_frame(response)
    if frame.empty:
        return
    with st.expander("View as a table"):
        first, last = frame.iloc[0], frame.iloc[-1]
        change = (last["close"] - first["close"]) / first["close"] * 100
        st.caption(
            f"{response.symbol}: {len(frame):,} bars from "
            f"{frame.index[0]:%Y-%m-%d} to {frame.index[-1]:%Y-%m-%d}. "
            f"Close {first['close']:,.2f} to {last['close']:,.2f} {currency} "
            f"({change:+.2f}%). "
            f"High {frame['high'].max():,.2f}, low {frame['low'].min():,.2f}."
        )
        st.dataframe(
            frame.sort_index(ascending=False).round(2),
            width="stretch",
            height=280,
        )


def price_chart(response: HistoryResponse, currency: str = "USD") -> None:
    """Candlestick and volume, one figure, one x-axis, one crosshair.

    Used to be two independent figures stacked with a gap between them —
    each with its own x-axis, so zooming one left the other unsynced, and
    reading volume against a price move meant moving your eyes and guessing
    at alignment. make_subplots with shared_xaxes fixes both: one zoom,
    one hover, two panels that agree on where "now" is.
    """
    frame = bars_to_frame(response)
    if frame.empty:
        st.info("No price data in this range.", icon=ICON_EMPTY)
        return

    has_volume = frame["volume"].sum() > 0
    fig = (
        make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.78, 0.22],
            vertical_spacing=0.03,
        )
        if has_volume
        else make_subplots(rows=1, cols=1)
    )

    fig.add_trace(
        go.Candlestick(
            x=frame.index,
            open=frame["open"],
            high=frame["high"],
            low=frame["low"],
            close=frame["close"],
            name=response.symbol,
            increasing_line_color=theme.UP,
            decreasing_line_color=theme.DOWN,
        ),
        row=1,
        col=1,
    )

    if has_volume:
        # Each bar takes that day's own direction, not the range's overall
        # trend — a down day on rising volume should read as a down bar.
        bar_colors = [
            theme.UP if c >= o else theme.DOWN
            for o, c in zip(frame["open"], frame["close"], strict=True)
        ]
        fig.add_trace(
            go.Bar(
                x=frame.index,
                y=frame["volume"],
                marker_color=bar_colors,
                marker_opacity=0.35,
                name="Volume",
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=10, b=0),
        height=460 if has_volume else 420,
        hovermode="x unified",
        showlegend=False,
        # Inherit the viewer's Streamlit theme instead of hardcoding colours.
        template=None,
    )
    fig.update_yaxes(title_text=f"Price ({currency})", row=1, col=1)
    if has_volume:
        fig.update_yaxes(title_text="Volume", row=2, col=1)
    st.plotly_chart(fig, width="stretch")

    if response.count < response.total:
        st.caption(
            f"Showing {response.count:,} of {response.total:,} bars. Reduced with "
            f"LTTB, which preserves peaks and troughs rather than sampling evenly."
        )
    _data_table(response, currency)


def line(response: HistoryResponse, label: str, currency: str = "USD") -> None:
    frame = bars_to_frame(response)
    if frame.empty:
        st.info("No price data in this range.", icon=ICON_EMPTY)
        return

    # Coloured by the range's own trend, same vocabulary as every other
    # chart and tile in the app — never a decorative default blue. No area
    # fill: a crypto price rarely sits near zero, so "fill to zero" would
    # shade almost the entire visible plot rather than read as an accent.
    color = theme.direction_color(frame["close"].iloc[-1] - frame["close"].iloc[0]) or theme.ACCENT
    fig = go.Figure(
        data=[
            go.Scatter(
                x=frame.index,
                y=frame["close"],
                mode="lines",
                name=label,
                line=dict(color=color, width=1.6),
            )
        ]
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=380,
        yaxis_title=f"Price ({currency})",
        showlegend=False,
        template=None,
    )
    st.plotly_chart(fig, width="stretch")
    _data_table(response, currency)
