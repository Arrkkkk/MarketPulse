"""Markets — the price snapshot grid.

This used to render unconditionally at the top of every page, above a
sidebar radio labelled "Stocks / Cryptocurrencies" that did not govern it —
the control switched the detail section below, never this. It is what a
"Markets" destination actually shows now that the two are separate pages.
"""

from __future__ import annotations

import streamlit as st

from marketpulse.client import MarketPulseClientError
from marketpulse.ui.backend import get_client
from marketpulse.ui.components import panels, state

SNAPSHOT_STEP = 4

client = get_client()

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
    st.stop()
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
