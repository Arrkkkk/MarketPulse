"""Stock detail: search, chart, key metrics, news, AI analysis."""

from __future__ import annotations

import streamlit as st

from marketpulse.client import MarketPulseClientError
from marketpulse.schema.exchanges import currency_for_symbol
from marketpulse.schema.market import Quote
from marketpulse.ui.backend import get_client, service_info
from marketpulse.ui.components import analysis, charts, panels, search, state

client = get_client()

st.subheader("Stock detail")

raw = search.symbol_input(client)
if not raw:
    state.show_empty("Search for a company or enter a ticker to see details.")
    st.stop()

period = charts.range_selector("stock_range", charts.RANGE_OPTIONS, default="1Y")

profile = None
try:
    try:
        profile = client.get_profile(raw)
    except MarketPulseClientError as exc:
        # Profile is decoration: a missing one greys out a panel rather
        # than failing the page.
        if exc.code != "symbol_not_found":
            st.caption(f"Company details unavailable: {exc}")

    chart_slot = st.empty()
    with chart_slot.container():
        state.skeleton_chart()
    history = client.get_history(raw, period=period)
    chart_slot.empty()
except MarketPulseClientError as exc:
    state.show_error(exc, context=raw)
    st.stop()

panels.company_header(raw, profile)

currency = (profile.currency if profile else None) or currency_for_symbol(raw) or ""
frame_bars = history.bars
if frame_bars:
    last, prev = frame_bars[-1], (frame_bars[-2] if len(frame_bars) > 1 else None)
    panels.key_metrics(
        Quote(
            symbol=raw,
            price=last.c,
            previous_close=prev.c if prev else None,
            open=last.o,
            high=last.h,
            low=last.low,
            volume=int(last.v),
            currency=currency,
            as_of=last.t,
            cached=history.cached,
        ),
        profile,
    )

charts.candlestick(history, currency)
charts.volume(history)
state.freshness_caption(history.as_of, history.cached)

st.markdown("##### Recent news")
try:
    panels.news_panel(client.get_news(raw))
except MarketPulseClientError as exc:
    state.show_error(exc, context="News")

st.markdown("##### AI analysis")
analysis.analysis_panel(client, raw, ai_enabled=bool(service_info().get("ai_enabled")))
