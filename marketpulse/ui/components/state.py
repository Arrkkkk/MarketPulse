"""Loading, empty, error and stale states — rendered consistently.

The old UI had one state: whatever `st.info` string happened to be nearest.
A rate-limited provider and a company with no coverage both produced "No
recent news found", which was false in the first case.

These helpers make the four states distinct and give each one an honest
message, driven by the error code the service returned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from marketpulse.client import MarketPulseClientError

#: Human wording per service error code. Anything unmapped falls back to the
#: service's own message, which is already written for display.
_MESSAGES = {
    "symbol_not_found": (
        "No data for that symbol. Check the ticker and its exchange suffix "
        "(for example `RELIANCE.NS` for India, `0005.HK` for Hong Kong)."
    ),
    "rate_limited": "The data provider is rate-limiting us. This usually clears in a minute.",
    "provider_unavailable": "The data provider is not responding right now.",
    "provider_circuit_open": (
        "Requests to that provider are paused because it has been failing. "
        "Service resumes automatically."
    ),
    "provider_not_configured": "This feature needs an API key that is not configured.",
}


def show_error(exc: MarketPulseClientError, *, context: str = "") -> None:
    """Render a service error as something a user can act on."""
    message = _MESSAGES.get(exc.code or "", str(exc))
    prefix = f"**{context}** — " if context else ""

    if exc.code == "symbol_not_found":
        st.info(f"{prefix}{message}")
    elif exc.code in ("rate_limited", "provider_circuit_open"):
        wait = f" Try again in about {exc.retry_after}s." if exc.retry_after else ""
        st.warning(f"{prefix}{message}{wait}")
    elif exc.code == "provider_not_configured":
        st.info(f"{prefix}{message}")
    elif exc.code is None:
        st.error(
            f"{prefix}Cannot reach the MarketPulse API. Is it running? "
            f"Start it with `uv run uvicorn marketpulse.api.main:app`."
        )
    else:
        st.error(f"{prefix}{message}")

    if exc.request_id:
        st.caption(f"Request ID `{exc.request_id}`")


def show_empty(message: str) -> None:
    """Genuinely nothing to show — distinct from a failure."""
    st.info(message)


def freshness_caption(as_of: datetime | None, cached: bool) -> None:
    """Say how old the data is and whether it came from cache.

    The old UI printed "Next automatic data update in approx. N seconds",
    computed from a stale timestamp, and cheerfully went negative.
    """
    if as_of is None:
        return
    now = datetime.now(UTC)
    stamp = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    age = (now - stamp).total_seconds()

    if age < 90:
        ago = f"{int(age)}s ago"
    elif age < 3600:
        ago = f"{int(age // 60)}m ago"
    elif age < 86400:
        ago = f"{int(age // 3600)}h ago"
    else:
        ago = stamp.strftime("%Y-%m-%d")

    st.caption(f"{'Cached · ' if cached else ''}Data as of {ago}")


def degraded_banner(failures: dict[str, str]) -> None:
    """Name what is missing, instead of rendering a silent empty panel."""
    if not failures:
        return
    labels = {
        "stocks": "Stock data",
        "crypto": "Crypto data",
        "missing_symbols": "Some symbols returned no data",
    }
    lines = [f"- **{labels.get(k, k)}**: {v}" for k, v in failures.items()]
    st.warning("Some data could not be loaded:\n" + "\n".join(lines))
