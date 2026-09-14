"""Streamlit entrypoint.

    uv run streamlit run app.py

Kept at the repo root because that is where Streamlit and every deployment
target expect to find it. The dashboard itself lives in marketpulse/ui.
"""

from marketpulse.ui.app import main

main()
