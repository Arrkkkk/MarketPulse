"""Which models exist, what they cost, and which key they need.

Sourced from `models.json` next to this module, so the catalog, the cost
table and the fallback chain are one artifact rather than three constants
that drift.

The old code hardcoded `gemini-1.5-flash` at import time in api_utils.py, so
changing model meant editing source, and there was no fallback when that one
model was unavailable.

Adapted from virattt/ai-hedge-fund `hedge_fund/llm/registry.py`, including
its failure posture: a malformed catalog costs you the picker, not the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from marketpulse.platform.telemetry import get_logger

logger = get_logger("ai.registry")

CATALOG_PATH = Path(__file__).resolve().parent / "models.json"

#: Provider -> the environment variable its credential is read from.
PROVIDER_ENV_VARS = {"google": "GEMINI_API_KEY"}

#: Used when the catalog is missing or unreadable. Keeping the app alive on a
#: known-good id beats failing to start over a data file.
_FALLBACK_MODEL_ID = "gemini-2.5-flash"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    provider: str = "google"
    default: bool = False
    fallback: bool = False
    supports_structured_output: bool = True
    supports_streaming: bool = True
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None

    @property
    def env_var(self) -> str | None:
        return PROVIDER_ENV_VARS.get(self.provider)

    @property
    def has_pricing(self) -> bool:
        return self.input_usd_per_mtok is not None and self.output_usd_per_mtok is not None

    def estimate_cost_usd(self, prompt_tokens: int, output_tokens: int) -> float | None:
        """Estimated USD for one call, or None when rates are unknown.

        None rather than 0.0: a missing rate must not read as "this was free".
        """
        if not self.has_pricing:
            return None
        return (
            prompt_tokens / 1_000_000 * self.input_usd_per_mtok
            + output_tokens / 1_000_000 * self.output_usd_per_mtok
        )


@lru_cache(maxsize=1)
def load_catalog() -> tuple[ModelSpec, ...]:
    """Every known model, in file order."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        specs = tuple(
            ModelSpec(
                id=entry["id"],
                display_name=entry.get("display_name", entry["id"]),
                provider=entry.get("provider", "google"),
                default=entry.get("default", False),
                fallback=entry.get("fallback", False),
                supports_structured_output=entry.get("supports_structured_output", True),
                supports_streaming=entry.get("supports_streaming", True),
                input_usd_per_mtok=entry.get("input_usd_per_mtok"),
                output_usd_per_mtok=entry.get("output_usd_per_mtok"),
            )
            for entry in raw.get("models", [])
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("model catalog unreadable (%s); using the built-in default", exc)
        specs = ()

    if not specs:
        return (
            ModelSpec(
                id=_FALLBACK_MODEL_ID, display_name=_FALLBACK_MODEL_ID, default=True
            ),
        )
    return specs


def get_model(model_id: str) -> ModelSpec | None:
    return next((m for m in load_catalog() if m.id == model_id), None)


def default_model() -> ModelSpec:
    catalog = load_catalog()
    return next((m for m in catalog if m.default), catalog[0])


def fallback_model() -> ModelSpec | None:
    """The model to try when the primary fails.

    A different model, not a retry of the same one: retrying an unavailable
    model does not make it available, and the point of a fallback is to be a
    different failure domain.
    """
    return next((m for m in load_catalog() if m.fallback), None)


def resolve(model_id: str | None) -> ModelSpec:
    """Turn a configured id into a spec, tolerating unknown ids.

    An operator who sets GEMINI_MODEL to something newer than this catalog
    should get that model, not an argument. Unknown ids are wrapped in a
    spec with no pricing, so they work and simply report no cost estimate.
    """
    if not model_id:
        return default_model()
    known = get_model(model_id)
    if known:
        return known
    logger.info("model %r is not in the catalog; using it with no cost estimate", model_id)
    return ModelSpec(id=model_id, display_name=model_id)
