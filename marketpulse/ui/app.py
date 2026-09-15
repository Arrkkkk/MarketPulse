"""Streamlit entrypoint.

    uv run streamlit run app.py

Kept at the repo root because that is where Streamlit and every deployment
target expect to find it. The dashboard itself lives in marketpulse/ui.

Acts as a router and frame of common elements — the masthead and sidebar
render here, on every page; st.navigation decides which page script under
ui/pages/ runs beneath them. Each page talks to the service through
`MarketPulseClient` and nothing else, same as this file always has: no
provider imports, no yfinance, no caching, no retry logic — all of that
lives behind the API.
"""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from marketpulse.client import MarketPulseClientError
from marketpulse.ui.backend import get_client
from marketpulse.ui.components import masthead, state
from marketpulse.ui.theme import inject_styles


def _sidebar() -> None:
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


def main() -> None:
    st.set_page_config(
        layout="wide",
        page_title="MarketPulse",
        page_icon="📈",
        initial_sidebar_state="expanded",
    )
    inject_styles()
    masthead.render(get_client())
    _sidebar()

    page = st.navigation(
        [
            st.Page(
                "marketpulse/ui/pages/markets.py",
                title="Markets",
                icon=":material/query_stats:",
                url_path="markets",
                default=True,
            ),
            st.Page(
                "marketpulse/ui/pages/stocks.py",
                title="Stocks",
                icon=":material/candlestick_chart:",
                url_path="stocks",
            ),
            st.Page(
                "marketpulse/ui/pages/crypto.py",
                title="Crypto",
                icon=":material/currency_bitcoin:",
                url_path="crypto",
            ),
            st.Page(
                "marketpulse/ui/pages/analyst.py",
                title="Analyst",
                icon=":material/psychology:",
                url_path="analyst",
            ),
        ],
        position="top",
    )
    page.run()


if __name__ == "__main__":
    main()
