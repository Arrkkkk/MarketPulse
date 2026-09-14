"""Chart rendering."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from marketpulse.client import bars_to_frame
from marketpulse.schema.api import HistoryResponse

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


def candlestick(response: HistoryResponse, currency: str = "USD") -> None:
    frame = bars_to_frame(response)
    if frame.empty:
        st.info("No price data in this range.")
        return

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=frame.index,
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name=response.symbol,
            )
        ]
    )
    fig.update_layout(
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=10, b=0),
        height=420,
        yaxis_title=f"Price ({currency})",
        # Inherit the viewer's Streamlit theme instead of hardcoding colours.
        template=None,
    )
    st.plotly_chart(fig, width="stretch")

    if response.count < response.total:
        st.caption(
            f"Showing {response.count:,} of {response.total:,} bars "
            f"(downsampled for transfer)."
        )


def line(response: HistoryResponse, label: str, currency: str = "USD") -> None:
    frame = bars_to_frame(response)
    if frame.empty:
        st.info("No price data in this range.")
        return
    fig = go.Figure(
        data=[go.Scatter(x=frame.index, y=frame["close"], mode="lines", name=label)]
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        height=380,
        yaxis_title=f"Price ({currency})",
    )
    st.plotly_chart(fig, width="stretch")


def volume(response: HistoryResponse) -> None:
    frame = bars_to_frame(response)
    if frame.empty or frame["volume"].sum() == 0:
        return
    fig = go.Figure(data=[go.Bar(x=frame.index, y=frame["volume"])])
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0), height=200, yaxis_title="Volume"
    )
    st.plotly_chart(fig, width="stretch")
