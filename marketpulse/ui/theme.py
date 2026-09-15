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

from pathlib import Path

import streamlit as st

_STYLESHEET = Path(__file__).resolve().parent.parent.parent / "static" / "css" / "marketpulse.css"

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

    st.markdown(unsafe_allow_html=True) with a raw <style> block — the same
    mechanism ui/components/state.py already uses for its skeleton
    placeholders, extended to a full stylesheet rather than one more
    injection idiom for this codebase to carry.

    Two alternatives were tried first and both looked fine but did nothing,
    which is why this is verified against a real rendered page (computed
    style, not just "no exception") rather than trusted on the API
    reading right:

    - `st.html(f'<link rel="stylesheet" href="...">')` — st.html content
      passes through DOMPurify, whose default allowlist drops <link>
      entirely.
    - `st.html(Path("...css"))` — st.html's own documented behaviour for a
      CSS file Path: wrap it in <style> tags. Confirmed correct at the
      Python level (AppTest shows the right <style> element), but that
      element never appeared in the live DOM — style-only st.html content
      is routed to what Streamlit calls an "event container", which this
      version does not render at all.

    Called unconditionally on every run rather than cached — Streamlit
    rebuilds the script's output tree each rerun, and a cached no-op here
    would mean the styles are only ever emitted once and then silently drop
    out of that tree on every rerun after the first.
    """
    st.markdown(f"<style>{_STYLESHEET.read_text()}</style>", unsafe_allow_html=True)
