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


#: Minimum characters before a name search fires. An exact ticker (the
#: branch above this gate) still resolves on its first keystroke — this
#: only holds back the fallback name-search API call, which used to fire
#: on every character typed, including the first.
_MIN_QUERY_LENGTH = 2


@st.fragment
def symbol_input(client: MarketPulseClient, *, key: str = "symbol_query") -> str | None:
    """Render the search box and return the chosen symbol, if any.

    Fragment-scoped: a keystroke here reruns only this box and its
    candidate list — not the chart, news and AI panel below, which used to
    refetch on every character typed while narrowing a company name. Those
    stay exactly as they were, showing the last confirmed symbol, until a
    *different* symbol is actually resolved — at which point st.rerun()
    (a full, unscoped rerun, deliberately not `scope="fragment"`) escalates
    so the rest of the page picks it up. Narrowing a query never does.

    The selection lives in session state so it also survives a Markets-page
    fragment refresh elsewhere in the app, which never touches this page.
    """
    previous = st.session_state.get("symbol")

    query = st.text_input(
        "Search a company or enter a ticker",
        value=st.session_state.get(key, "AAPL"),
        placeholder="apple · reliance · 0005.HK · ^GSPC",
        help="Type a company name to search, or an exact ticker to go straight there.",
    ).strip()

    if not query:
        return previous

    st.session_state[key] = query

    # An exact ticker needs no lookup. Anything with a space is a name.
    if is_valid_symbol(query) and " " not in query:
        resolved = query.upper()
        st.session_state["symbol"] = resolved
        _offer_alternatives(client, query)
        if resolved != previous:
            st.rerun()
        return resolved

    if len(query) < _MIN_QUERY_LENGTH:
        return previous

    try:
        matches = [
            m
            for m in client.search(query).matches
            if not m.quote_type or m.quote_type.upper() in _USEFUL_TYPES
        ]
    except MarketPulseClientError as exc:
        show_error(exc, context="Search")
        return previous

    if not matches:
        # A well-formed query that matched nothing — distinct from a search
        # that failed, which took the branch above.
        st.info(f"No symbols found for “{query}”. Try a company name or an exact ticker.")
        return previous

    labels = [m.label for m in matches]
    chosen = st.radio(
        "Matches",
        options=range(len(matches)),
        format_func=lambda i: labels[i],
        key=f"{key}_match",
        label_visibility="collapsed",
    )
    resolved = matches[chosen].symbol
    st.session_state["symbol"] = resolved
    if resolved != previous:
        st.rerun()
    return resolved


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
