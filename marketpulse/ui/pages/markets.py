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

#: Read once per full page run, from the sidebar slider in app.py. A widget
#: interaction elsewhere in the shared frame reruns this whole page script,
#: which re-decorates the fragment below with whatever the slider now says.
_REFRESH_SECONDS = st.session_state.get("refresh_interval", 60)


@st.fragment(run_every=_REFRESH_SECONDS)
def _snapshots() -> None:
    """The grid, on its own timer.

    Used to be `st_autorefresh` reloading the entire app every interval —
    the chart you were reading on the Stocks page, the symbol you'd
    searched, a streamed AI analysis, all discarded on someone else's
    clock. A fragment reruns only this function; every other page is
    simply never touched by this timer, because it isn't part of the tree
    Streamlit reruns for it.
    """
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
        return
    placeholder.empty()

    state.degraded_banner(overview.failures)
    limit = SNAPSHOT_STEP * st.session_state.snapshot_pages

    st.markdown("##### Stocks")
    panels.stock_snapshots(overview.stocks, limit)
    st.markdown("##### Cryptocurrencies")
    panels.crypto_snapshots(overview.crypto, limit)

    # Plain st.rerun(), not scope="fragment": per st.rerun's own docs, a
    # fragment-scoped rerun is only valid *during* a fragment-triggered
    # rerun, not during this one, which Streamlit is treating as a
    # full-app rerun (confirmed by scripts/smoke_ui.py, which caught the
    # StreamlitAPIException scope="fragment" raises otherwise). Unchanged
    # from before this fragment existed — it costs one redundant masthead
    # and sidebar refetch per pagination click, which is a fair trade for
    # not shipping a button that raises.
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


_snapshots()
