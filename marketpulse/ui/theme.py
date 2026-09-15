"""Design tokens, as Python.

Colour and type live declaratively in `.streamlit/config.toml` — see the
comment at the top of that file for why. This module exists for the values
config.toml *cannot* reach: Plotly figures are built in Python and need the
same hex codes, and the one hand-written stylesheet needs loading exactly
once.

Kept in sync with config.toml by hand, not generated from it, because the
two are read by two different systems (Streamlit's theme engine vs. this
process's own code) and a generator would be more machinery than four
constants justify. If these drift from config.toml, the chart colours and
the rest of the app will visibly disagree — that mismatch is the signal to
fix this file.
"""

from __future__ import annotations

import streamlit as st

#: Price rising. Reserved for that meaning alone — never decorative.
UP = "#0D7049"
UP_DARK = "#35B37E"

#: Price falling. Reserved for that meaning alone — never decorative.
DOWN = "#A8281F"
DOWN_DARK = "#E5695C"

#: Brand and interactive accent. Deliberately outside the red/green range
#: so it can never be misread as a market signal.
ACCENT = "#0E5F73"
ACCENT_DARK = "#4BADC4"

#: Degraded data, rate limits, stale cache — see ui/components/state.py.
CAUTION = "#8A5A00"
CAUTION_DARK = "#D6A44A"


def direction_color(value: float | None, *, dark: bool = False) -> str | None:
    """UP, DOWN, or None for a zero/unknown change — never guess a sign."""
    if value is None:
        return None
    if value > 0:
        return UP_DARK if dark else UP
    if value < 0:
        return DOWN_DARK if dark else DOWN
    return None


def inject_styles() -> None:
    """Load the one hand-written stylesheet.

    A <link> to a static file, not an inline <style> block: the browser
    fetches and caches it like any other asset instead of re-parsing a
    string on every rerun. Called unconditionally on every run rather than
    cached — Streamlit rebuilds the script's output tree each rerun, and a
    cached no-op here would mean the tag is only ever emitted once and then
    silently drops out of that tree on every rerun after the first.
    """
    st.html('<link rel="stylesheet" href="app/static/css/marketpulse.css">')
