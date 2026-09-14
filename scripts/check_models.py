#!/usr/bin/env python
"""Verify the model catalog against the live Gemini API.

`marketpulse/ai/models.json` was written without a key available, so the
model ids and the pricing in it are unverified. This script is how that gets
settled:

    GEMINI_API_KEY=... uv run python scripts/check_models.py

It lists what the API actually offers, flags catalog entries the API does not
recognise, and flags models that support generation but are missing from the
catalog. Pricing it cannot check — that has to come from the published rate
card — so it prints the current rates for a human to confirm.

Pattern from JoshuaC215/agent-service-toolkit `scripts/check_live_models.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from marketpulse.ai.registry import CATALOG_PATH, load_catalog
from marketpulse.config import get_settings, secret


def main() -> int:
    api_key = secret(get_settings().GEMINI_API_KEY)
    if not api_key:
        print("GEMINI_API_KEY is not set — nothing to check against.")
        print("Set it in .env or the environment and re-run.")
        return 2

    from google import genai

    client = genai.Client(api_key=api_key)

    live: dict[str, str] = {}
    try:
        for model in client.models.list():
            name = (model.name or "").removeprefix("models/")
            actions = set(getattr(model, "supported_actions", None) or [])
            if not actions or "generateContent" in actions:
                live[name] = getattr(model, "display_name", "") or name
    except Exception as exc:  # noqa: BLE001
        print(f"could not list models: {type(exc).__name__}: {exc}")
        return 1

    catalog = load_catalog()
    catalog_ids = {m.id for m in catalog}

    print(f"{len(live)} generation-capable models visible to this key\n")

    print("catalog entries:")
    missing = []
    for spec in catalog:
        mark = "ok " if spec.id in live else "MISSING"
        if spec.id not in live:
            missing.append(spec.id)
        rates = (
            f"in ${spec.input_usd_per_mtok}/Mtok  out ${spec.output_usd_per_mtok}/Mtok"
            if spec.has_pricing
            else "no pricing on file"
        )
        flags = []
        if spec.default:
            flags.append("default")
        if spec.fallback:
            flags.append("fallback")
        print(f"  {mark:8} {spec.id:28} {rates}" + (f"  [{', '.join(flags)}]" if flags else ""))

    uncatalogued = sorted(n for n in live if n not in catalog_ids)
    if uncatalogued:
        print(f"\navailable but not in the catalog ({len(uncatalogued)}):")
        for name in uncatalogued[:25]:
            print(f"    {name}")
        if len(uncatalogued) > 25:
            print(f"    … and {len(uncatalogued) - 25} more")

    verified_on = json.loads(CATALOG_PATH.read_text()).get("pricing_verified_on")
    print(f"\npricing_verified_on: {verified_on or 'null — RATES ARE UNVERIFIED'}")
    print(f"catalog: {Path(CATALOG_PATH).relative_to(Path.cwd())}")

    if missing:
        print(f"\nFAILED: {len(missing)} catalog model(s) not available: {missing}")
        return 1
    print("\nOK: every catalog model is available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
