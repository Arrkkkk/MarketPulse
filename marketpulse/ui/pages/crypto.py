"""Cryptocurrency detail: coin picker, chart, news."""

from __future__ import annotations

import streamlit as st

from marketpulse.client import MarketPulseClientError
from marketpulse.ui.backend import get_client
from marketpulse.ui.components import charts, panels, state

client = get_client()

st.subheader("Cryptocurrency detail")

try:
    coin_ids = client.info().default_crypto_ids
except MarketPulseClientError as exc:
    state.show_error(exc, context="Service")
    st.stop()

coin = st.selectbox(
    "Cryptocurrency",
    coin_ids,
    format_func=lambda c: c.replace("-", " ").title(),
)
days = charts.range_selector("crypto_range", charts.CRYPTO_RANGE_OPTIONS, default="1M")

try:
    chart_slot = st.empty()
    with chart_slot.container():
        state.skeleton_chart(16)
    history = client.get_crypto_history(coin, days=days)
    chart_slot.empty()
except MarketPulseClientError as exc:
    state.show_error(exc, context=coin)
    st.stop()

charts.line(history, coin.replace("-", " ").title())
state.freshness_caption(history.as_of, history.cached)

st.markdown("##### Recent news")
try:
    panels.news_panel(client.search_news(f"{coin.replace('-', ' ')} cryptocurrency"))
except MarketPulseClientError as exc:
    state.show_error(exc, context="News")
