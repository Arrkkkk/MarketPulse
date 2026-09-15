"""News domain models.

NewsAPI and MarketAux return different shapes; both providers normalise into
`NewsArticle` so nothing downstream has to know which one answered.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class NewsArticle(BaseModel):
    title: str
    url: str
    description: str | None = None
    source_name: str | None = None
    published_at: datetime | None = None

    @field_validator("published_at", mode="before")
    @classmethod
    def _parse_timestamp(cls, v: object) -> object:
        """Providers disagree on timestamp format; a bad one is not fatal."""
        if v in (None, ""):
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None


class NewsResult(BaseModel):
    """Articles plus provenance.

    `source` names which upstream answered and `fallback` records that the
    preferred one did not, so the UI can be honest about where the news came
    from instead of silently presenting second-choice results as first-choice.
    """

    articles: list[NewsArticle]
    source: str
    fallback: bool = False
    cached: bool = False

    def __len__(self) -> int:
        return len(self.articles)

    @property
    def empty(self) -> bool:
        return not self.articles
