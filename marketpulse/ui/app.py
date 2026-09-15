"""MarketPulse dashboard — presentation only.

Talks to the service through `MarketPulseClient` and nothing else. No
provider imports, no yfinance, no caching, no retry logic: all of that lives
behind the API now, which is why this file is a fifth of the size of the
542-line script it replaces.
"""

from __future__ import annotations

import os

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from marketpulse.client import DEFAULT_BASE_URL, MarketPulseClient, MarketPulseClientError
from marketpulse.schema.exchanges import currency_for_symbol
from marketpulse.schema.market import Quote
from marketpulse.ui.components import analysis, charts, panels, search, state

SNAPSHOT_STEP = 4


@st.cache_data(ttl=300)
def service_info() -> dict:
    """What the service can do. Cached: it does not change per rerun."""
    try:
        return get_client().info().model_dump()
    except MarketPulseClientError:
        return {}


@st.cache_resource
def get_client() -> MarketPulseClient:
    """One client per Streamlit process; httpx pools connections internally."""
    return MarketPulseClient(os.environ.get("MARKETPULSE_API_URL", DEFAULT_BASE_URL))


def _sidebar() -> tuple[str, int]:
    st.sidebar.header("Navigation")
    view = st.sidebar.radio("Market", ("Stocks", "Cryptocurrencies"))

    st.sidebar.header("Settings")
    interval = st.sidebar.slider("Auto-refresh (seconds)", 30, 300, 60, 30)

    # Client-side timer. The original parked a server thread in
    # time.sleep() per session, which capped concurrency at a handful.
    st_autorefresh(interval=interval * 1000, key="marketpulse_refresh")

    with st.sidebar.expander("Service status"):
        try:
            client = get_client()
            readiness = client.readiness()
            st.write(f"**{readiness.status}**")
            for name, value in readiness.checks.items():
                st.caption(f"{name}: {value}")
            st.caption(f"cache: {client.metrics()['cache']}")
        except MarketPulseClientError as exc:
            state.show_error(exc, context="Service")

    return view, interval


def _overview_section(client: MarketPulseClient) -> None:
    st.subheader("Market snapshots")

    if "snapshot_pages" not in st.session_state:
        st.session_state.snapshot_pages = 1

    placeholder = st.empty()
    with placeholder.container():
        state.skeleton_metrics(SNAPSHOT_STEP)
    try:
        overview = client.get_overview()
    except MarketPulseClientError as exc:
        placeholder.empty()
        state.show_error(exc, context="Overview")
        return
    placeholder.empty()

    state.degraded_banner(overview.failures)
    limit = SNAPSHOT_STEP * st.session_state.snapshot_pages

    st.markdown("##### Stocks")
    panels.stock_snapshots(overview.stocks, limit)
    st.markdown("##### Cryptocurrencies")
    panels.crypto_snapshots(overview.crypto, limit)

    total = max(len(overview.stocks), len(overview.crypto))
    left, right = st.columns(2)
    with left:
        if limit < total and st.button("Show more", width="stretch"):
            st.session_state.snapshot_pages += 1
            st.rerun()
    with right:
        if st.session_state.snapshot_pages > 1 and st.button("Show less", width="stretch"):
            st.session_state.snapshot_pages -= 1
            st.rerun()

    if overview.stocks:
        state.freshness_caption(
            max(q.as_of for q in overview.stocks),
            any(q.cached for q in overview.stocks),
        )


def _stock_detail(client: MarketPulseClient) -> None:
    st.subheader("Stock detail")

    raw = search.symbol_input(client)
    if not raw:
        state.show_empty("Search for a company or enter a ticker to see details.")
        return

    period = charts.range_selector("stock_range", charts.RANGE_OPTIONS, default="1Y")

    try:
        profile = None
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
        return

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
    analysis.analysis_panel(
        client, raw, ai_enabled=bool(service_info().get("ai_enabled"))
    )


def _crypto_detail(client: MarketPulseClient) -> None:
    st.subheader("Cryptocurrency detail")

    try:
        coin_ids = client.info().default_crypto_ids
    except MarketPulseClientError as exc:
        state.show_error(exc, context="Service")
        return

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
        return

    charts.line(history, coin.replace("-", " ").title())
    state.freshness_caption(history.as_of, history.cached)

    st.markdown("##### Recent news")
    try:
        panels.news_panel(client.search_news(f"{coin.replace('-', ' ')} cryptocurrency"))
    except MarketPulseClientError as exc:
        state.show_error(exc, context="News")


def main() -> None:
    st.set_page_config(
        layout="wide",
        page_title="MarketPulse",
        page_icon="📈",
        initial_sidebar_state="expanded",
    )
    st.title("📈 MarketPulse")
    st.caption("Real-time stock and cryptocurrency tracking with news aggregation")

    view, _ = _sidebar()
    client = get_client()

    _overview_section(client)
    st.divider()

    if view == "Stocks":
        _stock_detail(client)
    else:
        _crypto_detail(client)

    st.divider()
    analysis.insights_panel(client, ai_enabled=bool(service_info().get("ai_enabled")))


if __name__ == "__main__":
    main()
