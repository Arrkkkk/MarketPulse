#!/usr/bin/env python
"""End-to-end UI smoke test: drives app.py against a running API.

Uses Streamlit's own AppTest harness, so this exercises the real script,
the real client, and the real service — the whole Phase 3 split in one go.

    uv run uvicorn marketpulse.api.main:app --port 8000 &
    uv run python scripts/smoke_ui.py

Set MARKETPULSE_API_URL to point at a service on another port.

Since Phase 12, app.py is a router: st.navigation dispatches to a page
script under marketpulse/ui/pages/, so most of this drives page switches
rather than sidebar controls. AppTest.switch_page() takes a path relative
to the entrypoint (repo root), matching what app.py itself passes to
st.Page — same convention, same relative root.

Adapted from JoshuaC215/agent-service-toolkit `scripts/e2e_ui_tests.py`.
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from streamlit.testing.v1 import AppTest  # noqa: E402

# AppTest resolves relative paths against THIS file, not the cwd.
APP = str(Path(__file__).resolve().parent.parent / "app.py")

STOCKS_PAGE = "marketpulse/ui/pages/stocks.py"
CRYPTO_PAGE = "marketpulse/ui/pages/crypto.py"
ANALYST_PAGE = "marketpulse/ui/pages/analyst.py"


def run(label: str, fn) -> bool:
    start = time.perf_counter()
    try:
        at = fn()
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:34} EXCEPTION {type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - start
    if at.exception:
        print(f"  {label:34} FAILED ({elapsed:.1f}s)")
        for e in at.exception:
            print(f"      {e.type}: {str(e.message)[:200]}")
        return False
    print(f"  {label:34} ok ({elapsed:.1f}s)")
    return True


def main() -> int:
    ok = True
    print("driving app.py through AppTest\n")

    def initial():
        at = AppTest.from_file(APP, default_timeout=120)
        at.run()
        return at

    first = AppTest.from_file(APP, default_timeout=120)
    first.run()
    ok &= run("initial load (Markets)", lambda: first)

    if first.exception:
        return 1

    # The default page — Markets — must have rendered real numbers, not an
    # error state.
    metrics = [m.label for m in first.metric]
    print(f"      {len(metrics)} metric tiles rendered")
    if not metrics:
        print("      FAILED: no metrics rendered — the UI is showing an error state")
        ok = False

    def click(label_fragment: str):
        at = AppTest.from_file(APP, default_timeout=120)
        at.run()
        for button in at.button:
            if label_fragment.lower() in button.label.lower():
                button.click().run()
                break
        return at

    ok &= run("click 'Show more' (Markets)", lambda: click("show more"))
    ok &= run("navigate to Stocks", lambda: _goto(initial(), STOCKS_PAGE))
    ok &= run("navigate to Crypto", lambda: _goto(initial(), CRYPTO_PAGE))
    ok &= run("navigate to Analyst", lambda: _goto(initial(), ANALYST_PAGE))
    ok &= run("exact ticker", lambda: _search(_goto(initial(), STOCKS_PAGE), "MSFT"))
    ok &= run("company name search", lambda: _search(_goto(initial(), STOCKS_PAGE), "reliance"))
    ok &= run(
        "query matching nothing", lambda: _search(_goto(initial(), STOCKS_PAGE), "zzzznotacompany")
    )
    ok &= run("query with punctuation", lambda: _search(_goto(initial(), STOCKS_PAGE), "!!!!"))

    print("\n" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def _goto(at, page_path: str):
    at.switch_page(page_path).run()
    return at


def _search(at, symbol: str):
    if at.text_input:
        at.text_input[0].set_value(symbol).run()
    return at


if __name__ == "__main__":
    sys.exit(main())
