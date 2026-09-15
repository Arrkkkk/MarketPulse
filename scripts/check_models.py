#!/usr/bin/env python
"""Verify the model catalog against the live Gemini API.

    GEMINI_API_KEY=... uv run python scripts/check_models.py

This calls `generateContent` with a real `response_schema` for every catalog
entry, because listing is not enough: `gemini-2.5-flash` appears in
`models.list()` and returns 404 NOT_FOUND when actually invoked. The first
version of this script only checked the list and would have passed a catalog
full of unusable ids.

Pricing it cannot check — that has to come from the published rate card —
so it reports which entries still have no rates on file.

Pattern from JoshuaC215/agent-service-toolkit `scripts/check_live_models.py`.
"""

from __future__ import annotations

import json
import sys
import time
import warnings

warnings.filterwarnings("ignore")

from marketpulse.ai.prompts import build_analysis_prompt  # noqa: E402
from marketpulse.ai.registry import CATALOG_PATH, load_catalog  # noqa: E402
from marketpulse.ai.schemas import NewsAnalysis  # noqa: E402
from marketpulse.config import get_settings, secret  # noqa: E402
from marketpulse.schema.news import NewsArticle  # noqa: E402

#: A 503 from Gemini is usually transient capacity, so give it one retry
#: before calling a model unusable.
_RETRIES_ON_503 = 1

PROBE_ARTICLES = [
    NewsArticle(
        title="Company beats quarterly earnings expectations",
        url="https://example.com/1",
        description="Revenue came in above consensus.",
    ),
    NewsArticle(
        title="Regulators open an investigation",
        url="https://example.com/2",
        description="Authorities announced a formal probe.",
    ),
]


def probe(client, model_id: str, prompt: str) -> tuple[bool, float, str]:
    """Actually call the model. Returns (usable, latency_ms, note)."""
    for attempt in range(_RETRIES_ON_503 + 1):
        started = time.perf_counter()
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": NewsAnalysis,
                },
            )
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "503" in text and attempt < _RETRIES_ON_503:
                time.sleep(2)
                continue
            code = next((c for c in ("404", "403", "429", "503") if c in text), "")
            return False, 0.0, f"{code or type(exc).__name__}"
        elapsed = (time.perf_counter() - started) * 1000
        if not isinstance(response.parsed, NewsAnalysis):
            return False, elapsed, "responded but did not satisfy the schema"
        return True, elapsed, response.parsed.sentiment.value
    return False, 0.0, "503 after retry"


def main() -> int:
    api_key = secret(get_settings().GEMINI_API_KEY)
    if not api_key:
        print("GEMINI_API_KEY is not set — nothing to check against.")
        print("Set it in .env or the environment and re-run.")
        return 2

    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = build_analysis_prompt("Example Corp", PROBE_ARTICLES)
    catalog = load_catalog()

    try:
        listed = {
            (m.name or "").removeprefix("models/")
            for m in client.models.list()
            if not getattr(m, "supported_actions", None)
            or "generateContent" in set(m.supported_actions)
        }
    except Exception as exc:  # noqa: BLE001
        print(f"could not list models: {type(exc).__name__}: {exc}")
        listed = set()

    print(f"{len(listed)} generation-capable models listed for this key")
    print("probing each catalog entry with a real structured call\n")

    print(f"{'':8} {'model':28} {'latency':>9}  {'rates':>9}  note")
    print("-" * 78)

    broken: list[str] = []
    unpriced: list[str] = []
    for spec in catalog:
        usable, latency, note = probe(client, spec.id, prompt)
        if not usable:
            broken.append(spec.id)
        if not spec.has_pricing:
            unpriced.append(spec.id)

        flags = [f for f, on in (("default", spec.default), ("fallback", spec.fallback)) if on]
        print(
            f"{'OK' if usable else 'BROKEN':8} {spec.id:28} "
            f"{(f'{latency:.0f}ms' if latency else '-'):>9}  "
            f"{('yes' if spec.has_pricing else 'none'):>9}  "
            f"{note}" + (f"  [{', '.join(flags)}]" if flags else "")
        )
        if spec.id not in listed and listed:
            print(f"{'':8} {'':28} {'':>9}  {'':>9}  (not in models.list())")

    meta = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    print(f"\nverified_on:         {meta.get('verified_on') or 'never'}")
    print(f"pricing_verified_on: {meta.get('pricing_verified_on') or 'null'}")

    if unpriced:
        print(
            f"\n{len(unpriced)} model(s) have no rates on file, so cost is reported as "
            "unavailable rather than estimated:"
        )
        print(f"    {', '.join(unpriced)}")
        print("    Fill them in from https://ai.google.dev/pricing to enable cost tracking.")

    if broken:
        print(f"\nFAILED: {len(broken)} catalog model(s) are not usable: {broken}")
        return 1
    print(f"\nOK: all {len(catalog)} catalog models answered with a valid schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
