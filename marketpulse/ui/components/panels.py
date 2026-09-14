"""Reusable display panels: snapshots, metrics, news."""

from __future__ import annotations

import streamlit as st

from marketpulse.schema.api import NewsResponse
from marketpulse.schema.market import CompanyProfile, CryptoQuote, Quote
from marketpulse.ui.components.state import freshness_caption

SNAPSHOT_COLUMNS = 4


def stock_snapshots(quotes: list[Quote], limit: int) -> None:
    """A grid of price tiles with day-over-day deltas."""
    shown = quotes[:limit]
    if not shown:
        st.info("No stock quotes available.")
        return
    columns = st.columns(SNAPSHOT_COLUMNS)
    for i, quote in enumerate(shown):
        with columns[i % SNAPSHOT_COLUMNS]:
            change = quote.change
            pct = quote.change_percent
            delta = (
                f"{change:+,.2f} ({pct:+.2f}%)"
                if change is not None and pct is not None
                else None
            )
            # No currency label when the venue is unknown — better a bare
            # number than one tagged with the wrong currency.
            label = f"{quote.symbol} ({quote.currency})" if quote.currency else quote.symbol
            st.metric(label=label, value=f"{quote.price:,.2f}", delta=delta)


def crypto_snapshots(quotes: list[CryptoQuote], limit: int) -> None:
    shown = quotes[:limit]
    if not shown:
        st.info("No crypto quotes available.")
        return
    columns = st.columns(SNAPSHOT_COLUMNS)
    for i, quote in enumerate(shown):
        with columns[i % SNAPSHOT_COLUMNS]:
            st.metric(
                label=f"{quote.display_name} (USD)",
                value=f"${quote.price:,.2f}",
                delta=(
                    f"{quote.change_percent_24h:+.2f}%"
                    if quote.change_percent_24h is not None
                    else None
                ),
            )


def key_metrics(quote: Quote, profile: CompanyProfile | None) -> None:
    """The detail view's metric block."""
    currency = quote.currency or ""
    c1, c2, c3 = st.columns(3)
    with c1:
        change, pct = quote.change, quote.change_percent
        st.metric(
            "Last close",
            f"{quote.price:,.2f} {currency}".strip(),
            delta=(
                f"{change:+,.2f} ({pct:+.2f}%)"
                if change is not None and pct is not None
                else None
            ),
        )
        if quote.open is not None:
            st.metric("Open", f"{quote.open:,.2f}")
    with c2:
        if quote.high is not None:
            st.metric("High", f"{quote.high:,.2f}")
        if quote.low is not None:
            st.metric("Low", f"{quote.low:,.2f}")
    with c3:
        if quote.volume is not None:
            st.metric("Volume", f"{quote.volume:,}")
        if quote.previous_close is not None:
            st.metric("Previous close", f"{quote.previous_close:,.2f}")

    if profile is None:
        return

    d1, d2, d3 = st.columns(3)
    with d1:
        if profile.market_cap:
            st.metric("Market cap", f"{profile.market_cap:,.0f}")
    with d2:
        if profile.fifty_two_week_high:
            st.metric("52-week high", f"{profile.fifty_two_week_high:,.2f}")
        if profile.fifty_two_week_low:
            st.metric("52-week low", f"{profile.fifty_two_week_low:,.2f}")
    with d3:
        if profile.trailing_pe:
            st.metric("Trailing P/E", f"{profile.trailing_pe:,.2f}")
        if profile.dividend_yield:
            st.metric("Dividend yield", f"{profile.dividend_yield * 100:.2f}%")


def company_header(symbol: str, profile: CompanyProfile | None) -> None:
    if profile is None or not profile.long_name:
        st.subheader(symbol)
        return

    st.subheader(f"{profile.long_name} ({symbol})")
    bits = [b for b in (profile.sector, profile.industry, profile.exchange) if b]
    if bits:
        st.caption(" · ".join(bits))

    if profile.summary:
        with st.expander("Business summary"):
            # Plain text. The old UI regex-bolded a hardcoded list of
            # Reliance Industries' business lines in every company's summary.
            st.write(profile.summary)


def news_panel(result: NewsResponse) -> None:
    """Articles with honest provenance."""
    if result.fallback:
        st.caption(
            f"Source: {result.source} — the preferred provider for this listing "
            f"had nothing or was unavailable."
        )
    else:
        st.caption(f"Source: {result.source}")

    if not result.articles:
        st.info("No recent articles found for this asset in the last 7 days.")
        return

    for article in result.articles:
        st.markdown(f"**[{article.title}]({article.url})**")
        meta = [m for m in (article.source_name,) if m]
        if article.published_at:
            meta.append(article.published_at.strftime("%Y-%m-%d %H:%M"))
        if meta:
            st.caption(" · ".join(meta))
        if article.description:
            st.write(article.description)
        st.divider()

    freshness_caption(None, result.cached)
