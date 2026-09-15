"""Ask the market analyst — free-text AI insights.

Used to render under a divider at the bottom of every page, regardless of
which view you were on. It gets its own destination now that navigation
is real.
"""

from __future__ import annotations

from marketpulse.ui.backend import get_client, service_info
from marketpulse.ui.components import analysis

analysis.insights_panel(get_client(), ai_enabled=bool(service_info().get("ai_enabled")))
