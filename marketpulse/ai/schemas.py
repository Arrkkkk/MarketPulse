"""Structured AI output.

The old implementation asked Gemini for prose and rendered whatever came
back with `st.write()`. Nothing about that is parseable, comparable,
chartable, storable or testable, and an error string
("Error getting Gemini insights: ...") was displayed to users as if it were
analysis.

Constraining the model to this schema makes the output data. Sentiment
becomes something you can chart over time, themes become chips, and a test
can assert the contract without asserting on prose — which is the only way
to test a non-deterministic system meaningfully.

`summary` is deliberately free text. Over-constraining an LLM flattens
nuance; the structured fields carry what needs to be machine-readable and
the summary carries what does not.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from marketpulse.schema.market import utcnow


class Sentiment(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class ArticleSentiment(BaseModel):
    """Per-article read, so a user can see which story drove the verdict."""

    title: str = Field(description="The article headline, echoed back")
    sentiment: Sentiment
    rationale: str = Field(description="One sentence on why", max_length=400)


class NewsAnalysis(BaseModel):
    """The contract the model must satisfy."""

    sentiment: Sentiment = Field(description="Overall sentiment across all articles")
    confidence: float = Field(
        ge=0.0, le=1.0, description="How strongly the articles support the verdict"
    )
    summary: str = Field(
        description="Two to four sentences on the themes and likely market impact",
        max_length=1500,
    )
    key_themes: list[str] = Field(
        default_factory=list, max_length=6, description="Short topic labels"
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Specific downside risks the articles raise",
    )
    per_article: list[ArticleSentiment] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """An analysis plus how it was produced.

    Provenance is part of the payload: which model answered, whether it was
    the fallback, how many tokens it cost, and whether this came from cache.
    A user looking at AI output should be able to see where it came from.
    """

    symbol: str
    analysis: NewsAnalysis
    model: str
    fallback: bool = False
    article_count: int = 0
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    #: USD, estimated from the registry's rate table. None when the model has
    #: no rate on file — an absent number beats an invented one.
    estimated_cost_usd: float | None = None
    latency_ms: int | None = None
    as_of: datetime = Field(default_factory=utcnow)
    cached: bool = False
