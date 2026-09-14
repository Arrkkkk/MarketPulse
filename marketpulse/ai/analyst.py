"""News analysis service.

Owns the policy around the model: what to cache, when to re-prompt, when to
fall back to a different model, and what to record about the call.

Three things the old `analyze_news_with_gemini` did not do:

- **Cache.** Analysis is the most expensive operation in the app and the
  least volatile — five articles that have not changed produce the same
  read. Keyed on the article content, so a new story invalidates it and a
  page refresh does not.
- **Repair.** A response that fails validation is re-asked once with the
  validation error attached, rather than surfaced as a failure.
- **Fall back.** If the primary model is unavailable, try the other one
  before giving up.
"""

from __future__ import annotations

import hashlib
import time

from marketpulse.ai.client import GeminiClient
from marketpulse.ai.errors import (
    AIContentFiltered,
    AIContextTooLong,
    AIInvalidOutput,
    AINotConfigured,
    AIUnavailable,
)
from marketpulse.ai.prompts import (
    build_analysis_prompt,
    build_insights_prompt,
    build_repair_prompt,
)
from marketpulse.ai.registry import ModelSpec, fallback_model, resolve
from marketpulse.ai.schemas import AnalysisResult, NewsAnalysis
from marketpulse.config import get_settings
from marketpulse.platform.cache import TTL_AI_ANALYSIS, Cache, get_cache, make_key
from marketpulse.platform.telemetry import get_logger
from marketpulse.schema.news import NewsArticle

logger = get_logger("ai.analyst")

#: One repair attempt. If naming the exact validation error does not fix it,
#: asking a third time is unlikely to, and each attempt costs tokens.
MAX_REPAIR_ATTEMPTS = 1


def articles_fingerprint(articles: list[NewsArticle]) -> str:
    """A stable id for a set of articles.

    URLs rather than titles: a publisher editing a headline should not
    invalidate the analysis, but a different story should.
    """
    joined = "|".join(sorted(a.url for a in articles))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


class NewsAnalyst:
    def __init__(
        self,
        client: GeminiClient | None = None,
        cache: Cache | None = None,
        model_id: str | None = None,
    ) -> None:
        self._client = client or GeminiClient()
        self._cache = cache or get_cache()
        self._model = resolve(model_id or get_settings().GEMINI_MODEL)

    @property
    def configured(self) -> bool:
        return self._client.configured

    @property
    def model(self) -> ModelSpec:
        return self._model

    def analyze(
        self, asset: str, articles: list[NewsArticle], *, refresh: bool = False
    ) -> AnalysisResult:
        """Structured sentiment analysis for a set of articles."""
        if not self.configured:
            raise AINotConfigured("GEMINI_API_KEY is not set")
        if not articles:
            # Nothing to analyse is not a failure, but it is also not
            # something to ask a model about.
            raise AIInvalidOutput("no articles were supplied to analyse")

        key = make_key(
            "ai:analysis",
            asset=asset,
            model=self._model.id,
            articles=articles_fingerprint(articles),
        )
        if not refresh:
            cached = self._cache.get(key)
            if cached is not None:
                return AnalysisResult.model_validate({**cached, "cached": True})

        result = self._analyze_uncached(asset, articles)
        self._cache.set(key, result.model_dump(mode="json"), TTL_AI_ANALYSIS)
        return result

    # -- internals ---------------------------------------------------------

    def _analyze_uncached(self, asset: str, articles: list[NewsArticle]) -> AnalysisResult:
        prompt = build_analysis_prompt(asset, articles)
        started = time.perf_counter()

        try:
            analysis, completion, spec = self._attempt(prompt, self._model)
            used_fallback = False
        except (AIUnavailable, AIInvalidOutput) as primary_error:
            secondary = fallback_model()
            if secondary is None or secondary.id == self._model.id:
                raise
            logger.warning(
                "%s failed (%s); falling back to %s",
                self._model.id,
                type(primary_error).__name__,
                secondary.id,
            )
            analysis, completion, spec = self._attempt(prompt, secondary)
            used_fallback = True

        return AnalysisResult(
            symbol=asset,
            analysis=analysis,
            model=spec.id,
            fallback=used_fallback,
            article_count=len(articles),
            prompt_tokens=completion.prompt_tokens,
            output_tokens=completion.output_tokens,
            estimated_cost_usd=completion.cost_usd(spec),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _attempt(self, prompt: str, spec: ModelSpec):
        """One model, with a bounded repair loop on schema failure."""
        current = prompt
        last_error: Exception | None = None

        for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
            completion = self._client.generate_structured(current, NewsAnalysis, spec)
            parsed = completion.parsed

            if isinstance(parsed, NewsAnalysis):
                return parsed, completion, spec

            # The SDK decoded nothing usable — a refusal, a truncation, or a
            # shape the schema rejected. Re-ask with the reason attached.
            reason = (
                "the response did not decode into the required schema; "
                f"raw text began: {completion.text[:200]!r}"
            )
            last_error = AIInvalidOutput(reason, model=spec.id)
            if attempt < MAX_REPAIR_ATTEMPTS:
                logger.info("schema validation failed on %s; re-asking", spec.id)
                current = build_repair_prompt(prompt, reason)

        raise last_error or AIInvalidOutput("no usable response", model=spec.id)

    # -- streaming ---------------------------------------------------------

    def stream_summary(self, asset: str, articles: list[NewsArticle]):
        """Stream a prose summary token by token.

        Separate from `analyze` on purpose. Streaming exists to cut perceived
        latency, and structured output cannot be shown until it is complete —
        a half-parsed JSON object is not a partial answer. So the UI streams
        prose for immediacy and requests the structured read alongside it.
        """
        if not self.configured:
            raise AINotConfigured("GEMINI_API_KEY is not set")
        prompt = build_analysis_prompt(asset, articles)
        yield from self._client.stream_text(prompt, self._model)

    def stream_insights(self, question: str):
        """Stream an answer to a free-text market question."""
        if not self.configured:
            raise AINotConfigured("GEMINI_API_KEY is not set")
        yield from self._client.stream_text(build_insights_prompt(question), self._model)


__all__ = [
    "AIContentFiltered",
    "AIContextTooLong",
    "NewsAnalyst",
    "articles_fingerprint",
]
