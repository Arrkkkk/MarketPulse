#!/usr/bin/env python
"""Live smoke check against the real upstreams.

Not part of `pytest` — the unit suite never touches the network. Run this by
hand (or in a scheduled CI job) to confirm the providers still work against
APIs that can change under us, and to re-measure the numbers quoted in the
README after a change.

    uv run python scripts/smoke_live.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from marketpulse.platform.cache import Cache  # noqa: E402
from marketpulse.providers.cached import (  # noqa: E402
    CachedCryptoProvider,
    CachedPriceProvider,
)
from marketpulse.providers.coingecko_provider import CoinGeckoProvider  # noqa: E402
from marketpulse.providers.errors import ProviderError, SymbolNotFound  # noqa: E402
from marketpulse.providers.yfinance_provider import YFinanceProvider  # noqa: E402
from marketpulse.services.market_service import MarketService  # noqa: E402

#: Cold-start time of the pre-Phase-1 code path, measured 2026-09-14.
BASELINE_SECONDS = 45.51


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "cache.sqlite3"
    cache = Cache(db_path=tmp)
    service = MarketService(
        CachedPriceProvider(YFinanceProvider(), cache),
        CachedCryptoProvider(CoinGeckoProvider(), cache),
    )
    failures: list[str] = []

    t0 = time.perf_counter()
    overview = service.get_overview()
    cold = time.perf_counter() - t0
    print(f"cold overview      {cold:6.2f}s  "
          f"stocks={len(overview.stocks)}/{len(service.stock_symbols)} "
          f"crypto={len(overview.crypto)}/{len(service.crypto_ids)}")
    if overview.degraded:
        print(f"  degraded: {overview.failures}")
    if not overview.stocks or not overview.crypto:
        failures.append("overview returned no data for one of the asset classes")

    t0 = time.perf_counter()
    warm = service.get_overview()
    warm_s = time.perf_counter() - t0
    print(f"warm overview      {warm_s:6.3f}s  all cached="
          f"{all(h.cached for h in warm.stocks.values())}")
    if warm_s > cold:
        failures.append("the warm path was not faster than the cold path")

    t0 = time.perf_counter()
    profile = service.get_profile("AAPL")
    print(f"lazy profile       {time.perf_counter() - t0:6.2f}s  "
          f"{profile.long_name if profile else 'MISSING'}")

    # The contract: a nonexistent symbol raises rather than answering empty.
    try:
        service.get_history("NOSUCHTICKERXYZ")
        failures.append("an unknown symbol returned data instead of raising")
    except SymbolNotFound:
        print("fail-loud contract   ok  SymbolNotFound raised for a bad symbol")
    except ProviderError as exc:
        print(f"fail-loud contract   ok  {type(exc).__name__} raised")

    print(f"\ncache {cache.stats()}")
    print(f"vs {BASELINE_SECONDS}s baseline: {BASELINE_SECONDS / max(cold, 0.01):.1f}x cold, "
          f"{BASELINE_SECONDS / max(warm_s, 0.001):.0f}x warm")

    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
