"""Reusable display panels: snapshots, metrics, news."""

from __future__ import annotations

import streamlit as st

from marketpulse.schema.api import NewsResponse
from marketpulse.schema.market import CompanyProfile, CryptoQuote, Quote
from marketpulse.ui.components.state import ICON_EMPTY, freshness_caption

SNAPSHOT_COLUMNS = 4

#: Session key holding the last price seen per tile, so a refresh can flash
#: what changed rather than just silently showing new numbers.
_LAST_PRICES = "_snapshot_last_prices"


def _direction_slug(change: float | None) -> str:
    """'up', 'down', or 'flat' for a zero/unknown change — never guess a sign."""
    if not change:
        return "flat"
    return "up" if change > 0 else "down"


def _flash_direction(track_key: str, price: float) -> str | None:
    """'up', 'down', or None if unchanged or first sight, from one refresh
    to the next — independent of the tile's day-over-day rail colour above.

    First sight returns None deliberately: flashing every tile on initial
    load would signal movement that never happened. `track_key` is a plain
    identity (symbol or coin id) rather than the CSS `key`, which encodes
    today's direction and would otherwise change identity the moment a
    price crosses its previous close.
    """
    previous: dict[str, float] = st.session_state.setdefault(_LAST_PRICES, {})
    before = previous.get(track_key)
    previous[track_key] = price
    if before is None or before == price:
        return None
    return "up" if price > before else "down"


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


def _sparkline_svg(points: list[float], *, width: int = 132, height: int = 32) -> str:
    """A recent-trend sparkline as inline SVG.

    No separate data table the way a full chart gets one (charts.py): the
    text alternative here is the aria-label itself, stating the actual
    move in words, which is a complete substitute for 30 points where it
    would only be a partial one for a year of OHLCV bars.

    Colour comes from `currentColor` plus a `mp-spark-{up,down}` class
    (marketpulse.css) rather than a literal hex, so it resolves correctly
    in both themes the same way everything else in this file does — never
    a hardcoded colour that only happens to work in one of them.
    """
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    n = len(points)
    xs = [i / (n - 1) * width for i in range(n)]
    ys = [height - ((p - lo) / span) * height for p in points]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys, strict=True))
    area = f"0,{height} {line} {width},{height}"

    up = points[-1] >= points[0]
    css_class = "mp-spark-up" if up else "mp-spark-down"
    pct = (points[-1] - points[0]) / points[0] * 100 if points[0] else 0.0
    label = (
        f"{n}-day trend: {'rose' if up else 'fell'} from {points[0]:,.2f} to "
        f"{points[-1]:,.2f}, {pct:+.1f}%"
    )

    return (
        f'<svg class="{css_class}" viewBox="0 0 {width} {height}" width="100%" '
        f'height="{height}" preserveAspectRatio="none" role="img" aria-label="{label}">'
        f'<polygon points="{area}" fill="currentColor" opacity="0.12"></polygon>'
        f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke">'
        f"</polyline>"
        f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2.4" fill="currentColor"></circle>'
        f"</svg>"
    )


def _price_tile(
    *,
    css_key: str,
    track_key: str,
    label: str,
    value: str,
    raw_price: float,
    delta: str | None,
    spark: list[float] | None = None,
) -> None:
    """A bordered surface with a rail on the edge that moved.

    st.metric stays the actual content — its own arrow glyph and signed
    number are the accessible, colour-independent half of the signal. The
    rail (marketpulse.css, keyed off `css_key`) is reinforcement, never the
    only signal.

    A refresh that actually changes the price also gets a brief flash wash
    — the one animation in this app that is pure information rather than
    decoration: on a grid of two dozen numbers, "what just changed" is
    otherwise unanswerable without staring at all of them at once.
    """
    flash = _flash_direction(track_key, raw_price)
    with st.container(border=True, key=css_key):
        if flash:
            st.markdown(f'<span class="mp-flash mp-flash--{flash}"></span>', unsafe_allow_html=True)
        st.metric(label=label, value=value, delta=delta)
        if spark and (svg := _sparkline_svg(spark)):
            st.markdown(svg, unsafe_allow_html=True)


def stock_snapshots(quotes: list[Quote], limit: int) -> None:
    """A grid of price tiles with day-over-day deltas."""
    shown = quotes[:limit]
    if not shown:
        st.info("No stock quotes available.", icon=ICON_EMPTY)
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
                css_key=f"tile-{_direction_slug(change)}-stock-{quote.symbol}",
                track_key=f"stock-{quote.symbol}",
                label=label,
                value=_format_price(quote.price),
                raw_price=quote.price,
                delta=delta,
                spark=quote.spark,
            )


def crypto_snapshots(quotes: list[CryptoQuote], limit: int) -> None:
    shown = quotes[:limit]
    if not shown:
        st.info("No crypto quotes available.", icon=ICON_EMPTY)
        return
    columns = st.columns(SNAPSHOT_COLUMNS)
    for i, quote in enumerate(shown):
        with columns[i % SNAPSHOT_COLUMNS]:
            _price_tile(
                css_key=f"tile-{_direction_slug(quote.change_percent_24h)}-crypto-{quote.coin_id}",
                track_key=f"crypto-{quote.coin_id}",
                label=f"{quote.display_name} (USD)",
                value=f"${_format_price(quote.price)}",
                raw_price=quote.price,
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
        st.info("No recent articles found for this asset in the last 7 days.", icon=ICON_EMPTY)
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
