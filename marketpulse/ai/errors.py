"""AI error taxonomy and classification.

The old code turned every AI failure into a string and returned it where the
analysis should be, so `st.write()` rendered "Error getting Gemini insights:
429 Resource exhausted" to the user as though it were market commentary.

These types let the API answer with a status code and let the analyst decide
what is worth a retry, a fallback model, or neither.

The SDK raises a wide and version-dependent set of exception types, so
classification is by message inspection. That is fragile by nature, which is
why the default is the conservative one: anything unrecognised is treated as
transient rather than silently swallowed. Same approach as
ZhuLinsen/daily_stock_analysis `src/llm/errors.py`.
"""

from __future__ import annotations


class AIError(Exception):
    """Base class for AI failures."""

    def __init__(self, message: str, *, model: str | None = None) -> None:
        self.model = model
        super().__init__(f"[{model or 'ai'}] {message}")


class AINotConfigured(AIError):
    """No API key. An operator has to fix this; never retried."""


class AIUnavailable(AIError):
    """Transport or server-side failure. Transient; worth a retry."""


class AIRateLimited(AIUnavailable):
    """Quota or requests-per-minute exceeded."""

    def __init__(
        self, message: str, *, model: str | None = None, retry_after: float | None = None
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, model=model)


class AIContextTooLong(AIError):
    """The prompt exceeded the model's window. Retrying unchanged is futile;
    the caller must send less."""


class AIContentFiltered(AIError):
    """The model declined to answer. A real answer, not a fault — and not
    something a retry will change."""


class AIInvalidOutput(AIError):
    """The model answered, but not in the shape the schema requires.

    The one error worth re-prompting on, because the correction can be fed
    back to the model. Bounded by the analyst's retry count.
    """


_RATE_LIMIT_MARKERS = ("429", "rate limit", "resource exhausted", "quota", "too many requests")
_CONTEXT_MARKERS = ("context length", "token limit", "too many tokens", "input is too long")
_FILTER_MARKERS = ("safety", "blocked", "content filter", "recitation", "prohibited")
_AUTH_MARKERS = ("api key", "unauthenticated", "permission denied", "401", "403")


def classify(exc: Exception, *, model: str | None = None) -> AIError:
    """Map a provider exception onto the taxonomy.

    Ordering matters: an auth failure often also mentions a status code, and
    "quota" can appear in a billing-disabled message, so the most specific
    and least retryable checks come first.
    """
    if isinstance(exc, AIError):
        return exc

    text = str(exc).lower()

    if any(m in text for m in _AUTH_MARKERS):
        return AINotConfigured(str(exc), model=model)
    if any(m in text for m in _CONTEXT_MARKERS):
        return AIContextTooLong(str(exc), model=model)
    if any(m in text for m in _FILTER_MARKERS):
        return AIContentFiltered(str(exc), model=model)
    if any(m in text for m in _RATE_LIMIT_MARKERS):
        return AIRateLimited(str(exc), model=model)

    # Unknown: assume transient. A wrongly-retried permanent failure costs a
    # few seconds; a wrongly-swallowed transient one costs the feature.
    return AIUnavailable(str(exc), model=model)
