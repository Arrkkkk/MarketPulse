"""Symbol search.

The original required you to already know that Reliance is `RELIANCE.NS` and
HSBC Hong Kong is `0005.HK`. Its help for that was a 60-row table of
exchange suffixes printed into the main page flow, which is documentation
standing in for a feature.

You can now type a company name. An exact ticker still works and skips the
lookup entirely, so people who know what they want are not slowed down by
people who do not.
"""

from __future__ import annotations

import streamlit as st

from marketpulse.client import MarketPulseClient, MarketPulseClientError
from marketpulse.schema.api import is_valid_symbol
from marketpulse.ui.components.state import show_error

#: Quote types worth offering. Yahoo also returns futures, options and
#: mutual funds, which this dashboard cannot chart usefully.
_USEFUL_TYPES = {"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY", "CURRENCY"}


def symbol_input(client: MarketPulseClient, *, key: str = "symbol_query") -> str | None:
    """Render the search box and return the chosen symbol, if any.

    The selection lives in session state so it survives the auto-refresh
    rerun; without that the page would reset to the default ticker every
    refresh interval.
    """
    query = st.text_input(
        "Search a company or enter a ticker",
        value=st.session_state.get(key, "AAPL"),
        placeholder="apple · reliance · 0005.HK · ^GSPC",
        help="Type a company name to search, or an exact ticker to go straight there.",
    ).strip()

    if not query:
        return st.session_state.get("symbol")

    st.session_state[key] = query

    # An exact ticker needs no lookup. Anything with a space is a name.
    if is_valid_symbol(query) and " " not in query:
        st.session_state["symbol"] = query.upper()
        _offer_alternatives(client, query)
        return st.session_state["symbol"]

    try:
        matches = [
            m
            for m in client.search(query).matches
            if not m.quote_type or m.quote_type.upper() in _USEFUL_TYPES
        ]
    except MarketPulseClientError as exc:
        show_error(exc, context="Search")
        return st.session_state.get("symbol")

    if not matches:
        # A well-formed query that matched nothing — distinct from a search
        # that failed, which took the branch above.
        st.info(f"No symbols found for “{query}”. Try a company name or an exact ticker.")
        return st.session_state.get("symbol")

    labels = [m.label for m in matches]
    chosen = st.radio(
        "Matches",
        options=range(len(matches)),
        format_func=lambda i: labels[i],
        key=f"{key}_match",
        label_visibility="collapsed",
    )
    st.session_state["symbol"] = matches[chosen].symbol
    return st.session_state["symbol"]


def _offer_alternatives(client: MarketPulseClient, ticker: str) -> None:
    """For an exact ticker, quietly offer the other listings of the same name.

    A user typing RELIANCE may want RELIANCE.NS; someone typing SHEL may
    want SHEL.L. Failures here are swallowed: this is a convenience, and it
    must not turn a working page into an error.
    """
    try:
        matches = [m for m in client.search(ticker, limit=5).matches if m.symbol != ticker.upper()]
    except MarketPulseClientError:
        return
    if not matches:
        return
    with st.expander(f"Other listings matching “{ticker}”"):
        for match in matches:
            if st.button(match.label, key=f"alt_{match.symbol}", width="stretch"):
                st.session_state["symbol"] = match.symbol
                st.session_state["symbol_query"] = match.symbol
                st.rerun()
