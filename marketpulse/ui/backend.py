"""Shared backend accessors.

Split out of app.py so that every page under ui/pages/ can reach the same
client and service metadata without importing the entrypoint module itself
— app.py is the router; it should not also be something pages import from.
"""

from __future__ import annotations

import os

import streamlit as st

from marketpulse.client import DEFAULT_BASE_URL, MarketPulseClient, MarketPulseClientError


@st.cache_resource
def get_client() -> MarketPulseClient:
    """One client per Streamlit process; httpx pools connections internally."""
    return MarketPulseClient(os.environ.get("MARKETPULSE_API_URL", DEFAULT_BASE_URL))


@st.cache_data(ttl=300)
def service_info() -> dict:
    """What the service can do. Cached: it does not change per rerun."""
    try:
        return get_client().info().model_dump()
    except MarketPulseClientError:
        return {}
