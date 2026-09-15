"""AI analysis panel.

Renders the structured verdict as a tinted band, chips and flags rather
than a wall of prose — which is the payoff for constraining the model's
output. Provenance (model, cost, cached) is shown, not hidden: a user
looking at AI-generated text should be able to see where it came from.
"""

from __future__ import annotations

import streamlit as st

from marketpulse.ai.schemas import AnalysisResult, Sentiment
from marketpulse.client import MarketPulseClient, MarketPulseClientError
from marketpulse.ui.components.state import ICON_DEGRADED, ICON_EMPTY, show_error

#: (glyph, label, CSS slug). "up"/"down" reuse the same rail and verdict-
#: band colours as everywhere else in the app that shows direction; the
#: rail additionally appears on a per-article row (see `_verdict` below).
#: "neutral"/"mixed" get a verdict-band tint of their own but no rail — a
#: rail asserts a direction, and neither sentiment is one.
_SENTIMENT_STYLE: dict[Sentiment, tuple[str, str, str]] = {
    Sentiment.BULLISH: ("▲", "Bullish", "up"),
    Sentiment.BEARISH: ("▼", "Bearish", "down"),
    Sentiment.NEUTRAL: ("■", "Neutral", "neutral"),
    Sentiment.MIXED: ("◆", "Mixed", "mixed"),
}
_UNKNOWN_STYLE = ("■", "Unknown", "neutral")


def _verdict(result: AnalysisResult, *, symbol: str) -> None:
    analysis = result.analysis
    icon, label, slug = _SENTIMENT_STYLE.get(analysis.sentiment, _UNKNOWN_STYLE)

    with st.container(border=True, key=f"verdict-{slug}-{symbol}"):
        left, right = st.columns([1, 2])
        with left:
            st.metric("Sentiment", f"{icon} {label}")
            st.progress(analysis.confidence, text=f"Confidence {analysis.confidence:.0%}")
        with right:
            st.write(analysis.summary)

    if analysis.key_themes:
        st.caption("Themes")
        with st.container(horizontal=True):
            for topic in analysis.key_themes:
                st.badge(topic, color="blue")

    if analysis.risk_flags:
        st.caption("Risks flagged")
        for flag in analysis.risk_flags:
            # Same glyph the rest of the app uses for anything degraded or
            # worth a second look — one small vocabulary, not a new one.
            st.markdown(f"⚠ {flag}")

    if analysis.per_article:
        with st.expander("Per-article read"):
            for i, item in enumerate(analysis.per_article):
                sub_icon, sub_label, item_slug = _SENTIMENT_STYLE.get(
                    item.sentiment, _UNKNOWN_STYLE
                )
                has_rail = item_slug in ("up", "down")
                key = f"tile-{item_slug}-article-{symbol}-{i}" if has_rail else None
                with st.container(border=True, key=key):
                    st.markdown(f"**{sub_icon} {sub_label}** — {item.title}")
                    st.caption(item.rationale)


def _provenance(result: AnalysisResult) -> None:
    # Backtick-wrapped: renders in the theme's code font and background, so
    # this reads as a measurement certificate rather than a footnote — the
    # same small-print treatment the news panel's byline uses.
    bits = [f"`{result.model}`"]
    if result.fallback:
        bits.append("`fallback model`")
    if result.cached:
        bits.append("`cached`")
    if result.prompt_tokens and result.output_tokens:
        bits.append(f"`{result.prompt_tokens + result.output_tokens:,} tokens`")
    if result.estimated_cost_usd is not None:
        # "estimated" is load-bearing: the rate table is not verified against
        # live pricing, and a number presented as exact would be a claim we
        # cannot support.
        bits.append(f"`~${result.estimated_cost_usd:.5f} estimated`")
    if result.latency_ms is not None:
        bits.append(f"`{result.latency_ms:,}ms`")
    with st.container(border=True):
        st.caption(" · ".join(bits))


def analysis_panel(client: MarketPulseClient, symbol: str, *, ai_enabled: bool) -> None:
    """Streamed summary first, then the structured verdict.

    Two calls on purpose. Structured output cannot be shown until it is
    complete — half a JSON object is not half an answer — so the stream
    carries prose for immediacy while the structured read follows.
    """
    if not ai_enabled:
        st.info(
            "AI analysis is unavailable: the server has no `GEMINI_API_KEY` configured.",
            icon=ICON_EMPTY,
        )
        return

    if not st.button(f"Analyse {symbol} news", key=f"analyse_{symbol}"):
        return

    # st.status labels the wait rather than leaving the page looking
    # frozen for the ~2.6s a cold analysis takes — expanded throughout,
    # not just while running, since the streamed summary underneath it is
    # real content to keep reading, not a process log to tuck away once
    # done. The polished verdict card renders after, outside the status
    # box: this brackets the raw AI process, the card is the answer.
    with st.status(f"Analysing {symbol} news…", expanded=True) as status:
        st.markdown("###### Summary")
        try:
            st.write_stream(client.stream_analysis(symbol))
        except MarketPulseClientError as exc:
            status.update(label="AI summary failed", state="error")
            show_error(exc, context="AI summary")
            return

        st.markdown("###### Structured read")
        try:
            result = client.get_analysis(symbol)
        except MarketPulseClientError as exc:
            status.update(label="AI analysis failed", state="error")
            show_error(exc, context="AI analysis")
            return

        status.update(label=f"Analysis of {symbol} complete", state="complete")

    _verdict(result, symbol=symbol)
    _provenance(result)


def insights_panel(client: MarketPulseClient, *, ai_enabled: bool) -> None:
    """The free-text market question box."""
    st.subheader("Ask the market analyst")
    if not ai_enabled:
        st.info("Unavailable: the server has no `GEMINI_API_KEY` configured.", icon=ICON_EMPTY)
        return

    question = st.text_area(
        "Question",
        value="What are the major trends driving global equity and crypto markets?",
        height=90,
        max_chars=1000,
    )
    if st.button("Ask", key="ask_insights"):
        if not question.strip():
            st.warning("Enter a question first.", icon=ICON_DEGRADED)
            return
        try:
            st.write_stream(client.stream_insights(question))
        except MarketPulseClientError as exc:
            show_error(exc, context="AI insights")
