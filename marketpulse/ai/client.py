"""Gemini client wrapper.

Thin by design. It knows how to call one provider under the platform's
resilience layer and how to report what a call cost; deciding *what* to ask
and how to recover from a bad answer belongs to the analyst.

Uses `google-genai`. The previous SDK, `google-generativeai`, is end of life
and prints a deprecation notice on import.

The underlying SDK calls are injected, so every test runs against a fake and
the suite never needs a key or a network.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from marketpulse.ai.errors import (
    AIContentFiltered,
    AIContextTooLong,
    AIError,
    AINotConfigured,
    classify,
)
from marketpulse.ai.registry import ModelSpec
from marketpulse.config import get_settings, secret
from marketpulse.platform.http import RetryPolicy, get_limiter, resilient
from marketpulse.platform.telemetry import get_logger

logger = get_logger("ai.client")

BREAKER_NAME = "gemini"

#: Self-imposed ceiling. Gemini's free tier is measured in requests per
#: minute, and the analysis cache means sustained load should be far lower.
_RATE_PER_SECOND = 0.5


@dataclass
class Completion:
    """One model response, with what it cost."""

    text: str
    parsed: BaseModel | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""

    def cost_usd(self, spec: ModelSpec) -> float | None:
        if self.prompt_tokens is None or self.output_tokens is None:
            return None
        return spec.estimate_cost_usd(self.prompt_tokens, self.output_tokens)


def _default_client_factory(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)


class GeminiClient:
    """Structured and streaming generation against Gemini."""

    def __init__(
        self,
        api_key: str | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else secret(settings.GEMINI_API_KEY)
        self._factory = client_factory or _default_client_factory
        self._client: Any | None = None
        self._limiter = get_limiter(BREAKER_NAME, _RATE_PER_SECOND, burst=3)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _sdk(self) -> Any:
        """Build the SDK client on first use, not at import.

        The old code called `genai.configure()` at module import, so the
        whole app carried a side effect of merely importing api_utils.
        """
        if not self.configured:
            raise AINotConfigured("GEMINI_API_KEY is not set")
        if self._client is None:
            self._client = self._factory(self._api_key)
        return self._client

    # -- generation --------------------------------------------------------

    def generate_structured(
        self, prompt: str, schema: type[BaseModel], model: ModelSpec
    ) -> Completion:
        """Ask for a response conforming to `schema`.

        The SDK enforces the schema at decode time and exposes the result as
        `.parsed`. A refusal or a truncated response leaves `.parsed` None,
        which the analyst treats as a repairable failure.
        """
        client = self._sdk()

        def _call() -> Any:
            return client.models.generate_content(
                model=model.id,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )

        response = self._guarded(_call, model)
        return Completion(
            text=getattr(response, "text", "") or "",
            parsed=getattr(response, "parsed", None),
            model=model.id,
            **_token_counts(response),
        )

    def generate_text(self, prompt: str, model: ModelSpec) -> Completion:
        client = self._sdk()

        def _call() -> Any:
            return client.models.generate_content(model=model.id, contents=prompt)

        response = self._guarded(_call, model)
        return Completion(
            text=getattr(response, "text", "") or "",
            model=model.id,
            **_token_counts(response),
        )

    def stream_text(self, prompt: str, model: ModelSpec) -> Iterator[str]:
        """Yield text chunks as the model produces them.

        Streaming is deliberately outside the retry wrapper. Once the first
        chunk has reached the caller a retry would repeat text already shown,
        so a mid-stream failure ends the stream rather than restarting it.
        """
        client = self._sdk()
        self._limiter.acquire()
        try:
            for chunk in client.models.generate_content_stream(
                model=model.id, contents=prompt
            ):
                text = getattr(chunk, "text", None)
                if text:
                    yield text
        except Exception as exc:  # noqa: BLE001
            raise classify(exc, model=model.id) from exc

    # -- internals ---------------------------------------------------------

    def _guarded(self, call: Callable[[], Any], model: ModelSpec) -> Any:
        """Run a call under rate limit, retry and breaker, mapping errors."""
        started = time.perf_counter()
        try:
            response = resilient(
                BREAKER_NAME,
                call,
                retry=RetryPolicy(attempts=3, base_delay=1.0),
                limiter=self._limiter,
                # Nothing a retry can fix.
                give_up_on=(AINotConfigured, AIContextTooLong, AIContentFiltered),
            )
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise classify(exc, model=model.id) from exc
        logger.info(
            "%s responded in %dms", model.id, int((time.perf_counter() - started) * 1000)
        )
        return response


def _token_counts(response: Any) -> dict[str, int | None]:
    """Pull usage off a response, tolerating a metadata shape that moves."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"prompt_tokens": None, "output_tokens": None}
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
    }
