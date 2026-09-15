"""Reusable display panels: snapshots, metrics, news."""

from __future__ import annotations

import streamlit as st

from marketpulse.schema.api import NewsResponse
from marketpulse.schema.market import CompanyProfile, CryptoQuote, Quote
from marketpulse.ui.components.state import freshness_caption

SNAPSHOT_COLUMNS = 4


def _direction_slug(change: float | None) -> str:
    """'up', 'down', or 'flat' for a zero/unknown change — never guess a sign."""
    if not change:
        return "flat"
    return "up" if change > 0 else "down"


def _format_price(value: float) -> str:
    """Two decimals below 10,000; none above.

    Cents on a $70,000 asset are false precision as much as they're visual
    noise — and in IBM Plex Mono's wider tabular figures (Phase 11), they
    were also the reason Bitcoin's tile value clipped behind an ellipsis.
    Below the threshold, every asset from Dogecoin to a $1,400 stock keeps
    its two decimals exactly as before.
    """
    if abs(value) >= 10_000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _format_large(value: float) -> str:
    """Abbreviated to K/M/B/T.

    Market cap and share volume in raw units are both harder to read at a
    glance and, at market-cap scale, too wide for a tile — AAPL's market
    cap as `4,861,029,515` clipped behind an ellipsis for the same reason
    Bitcoin's price did.
    """
    magnitude = abs(value)
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{value / threshold:,.2f}{suffix}"
    return f"{value:,.0f}"


def _price_tile(*, key: str, label: str, value: str, delta: str | None) -> None:
    """A bordered surface with a rail on the edge that moved.

    st.metric stays the actual content — its own arrow glyph and signed
    number are the accessible, colour-independent half of the signal. The
    rail (marketpulse.css, keyed off `key`) is reinforcement, never the only
    signal: direction is never colour alone anywhere in this app.
    """
    with st.container(border=True, key=key):
        st.metric(label=label, value=value, delta=delta)


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
                f"{change:+,.2f} ({pct:+.2f}%)" if change is not None and pct is not None else None
            )
            # No currency label when the venue is unknown — better a bare
            # number than one tagged with the wrong currency.
            label = f"{quote.symbol} ({quote.currency})" if quote.currency else quote.symbol
            _price_tile(
                key=f"tile-{_direction_slug(change)}-stock-{quote.symbol}",
                label=label,
                value=_format_price(quote.price),
                delta=delta,
            )


def crypto_snapshots(quotes: list[CryptoQuote], limit: int) -> None:
    shown = quotes[:limit]
    if not shown:
        st.info("No crypto quotes available.")
        return
    columns = st.columns(SNAPSHOT_COLUMNS)
    for i, quote in enumerate(shown):
        with columns[i % SNAPSHOT_COLUMNS]:
            _price_tile(
                key=f"tile-{_direction_slug(quote.change_percent_24h)}-crypto-{quote.coin_id}",
                label=f"{quote.display_name} (USD)",
                value=f"${_format_price(quote.price)}",
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
            f"{_format_price(quote.price)} {currency}".strip(),
            delta=(
                f"{change:+,.2f} ({pct:+.2f}%)" if change is not None and pct is not None else None
            ),
        )
        if quote.open is not None:
            st.metric("Open", _format_price(quote.open))
    with c2:
        if quote.high is not None:
            st.metric("High", _format_price(quote.high))
        if quote.low is not None:
            st.metric("Low", _format_price(quote.low))
    with c3:
        if quote.volume is not None:
            st.metric("Volume", _format_large(quote.volume))
        if quote.previous_close is not None:
            st.metric("Previous close", _format_price(quote.previous_close))

    if profile is None:
        return

    d1, d2, d3 = st.columns(3)
    with d1:
        if profile.market_cap:
            st.metric("Market cap", _format_large(profile.market_cap))
    with d2:
        if profile.fifty_two_week_high:
            st.metric("52-week high", _format_price(profile.fifty_two_week_high))
        if profile.fifty_two_week_low:
            st.metric("52-week low", _format_price(profile.fifty_two_week_low))
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
        # Each article is its own raised row — the border does the
        # separating, so a full-width divider between every item is no
        # longer needed to keep them from running together.
        with st.container(border=True):
            st.markdown(f"**[{article.title}]({article.url})**")
            meta = [m for m in (article.source_name,) if m]
            if article.published_at:
                meta.append(article.published_at.strftime("%Y-%m-%d %H:%M"))
            if meta:
                # Backtick-wrapped: renders in the theme's code font and
                # background, the same treatment the AI panel's provenance
                # strip uses — one small-print vocabulary for the whole app.
                st.caption(" · ".join(f"`{m}`" for m in meta))
            if article.description:
                st.write(article.description)

    freshness_caption(None, result.cached)
