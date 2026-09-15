"""The masthead: wordmark and a live status glance.

Readiness and cache hit rate already exist behind `client.readiness()` and
`client.metrics()` — until now the only place either was visible was a
collapsed sidebar expander. A markets tool should show its own pulse where
it is seen, not hide it behind a click. The sidebar expander stays too, for
the full per-provider breakdown; this is the glance, not the replacement.
"""

from __future__ import annotations

import streamlit as st

from marketpulse.client import MarketPulseClient, MarketPulseClientError


def render(client: MarketPulseClient) -> None:
    left, right = st.columns([3, 2], vertical_alignment="bottom")
    with left:
        st.title("MarketPulse")
        st.caption("Real-time stock and cryptocurrency tracking with news aggregation")
    with right:
        _status_line(client)


def _status_line(client: MarketPulseClient) -> None:
    try:
        readiness = client.readiness()
        cache = client.metrics()["cache"]
    except MarketPulseClientError:
        st.caption("✕ API unreachable")
        return

    bits = ["● Ready" if readiness.status == "ready" else "⚠ Degraded"]
    hit_rate = cache.get("hit_rate")
    if hit_rate is not None:
        bits.append(f"cache {hit_rate:.0%}")
    st.caption(" · ".join(bits))
